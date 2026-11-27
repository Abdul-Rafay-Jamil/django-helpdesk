# Section 4 · Defensive Security Assessment

The assessment uses the browser suite's synthetic SQLite data, queue, active non-superuser staff and customer fixtures. The tested build is fixed candidate `bec9c208546c127e0a36c344779113e9b4c8b601` plus the previously verified intake-landmark template change (blob `287cf6b5b6b19c356c3cfa91cecf0d325c0ef905`). Application source stayed unchanged during this phase. [Browser workflows](../browser-2026-11-27/README.md) · [Intake patch](../accessibility-2026-11-27/intake-landmark.patch)

## Attack surface and access decisions

| Workflow | Entry points and trust boundaries | Protected data or action |
| --- | --- | --- |
| W1 · intake and assignment | Public multipart `/tickets/submit/`; staff detail `/tickets/{id}/` and update `/tickets/{id}/update/`; session, queue selection, submitter email, user-supplied text, upload and assignee inputs. | Create a record in a public queue, restrict staff assignment to eligible users and tickets, preserve ownership on denial. |
| W2 · response, resolution and review | Staff detail and shared update route; customer receipt and optional lookup at `/view/`; session, ticket ID, submitter identity, receipt key, comment, status and visibility inputs. | Preserve ticket identity, confine private notes to authorized staff, restrict staff resolutions and return the authorized customer projection. |
| Related API surface · W1 and W2 | `/api/tickets/`, `/api/tickets/{id}/` and `/api/followups/` when enabled. | Staff API permissions, ticket queryset scoping, tenant data and write integrity. [Separate API results](../api-2026-11-27/README.md#requirement-traceability) |

| Decision | Customer / non-staff | Support staff | Playwright case and check |
| --- | --- | --- | --- |
| Submit via W1 (FR01–FR03) | Allow for a public queue, including anonymous intake under this configuration | Staff intake available under its own configuration | UC01-M exercises public intake and reads the ticket and opening note back. |
| Assign in W1 (FR04–FR05) | Deny staff detail and assignment UI | Allow on a ticket in scope | UC02-E1 verifies redirect, hidden detail/control, unchanged state and history. UC02-M checks visible and stored assignment and owner audit. |
| Comment in W2 (FR07, FR09) | Allow own ticket public reply | Allow public or private reply within scope | UC03-A2 checks visible and persisted customer reply. Staff results are in the existing UC03 report. |
| Resolve in W2 (FR08–FR09) | Deny staff resolution action | Allow on a ticket in scope | UC04-E1 verifies the option is absent and the ticket/history unchanged. UC04-M checks visible resolution, status and history persisted. |
| Read receipts and private history in W2 (FR06, FR09) | Allow own receipt and public history, deny private notes and protected tickets of others | Allow detail and internal history within scope | UC06-M verifies public projection, hidden private note and no writes. Historical UC06-E2 already verified another customer got no ticket and no writes before failing its separate error-feedback assertion (UI-02). |

Scope is the two workflows on loopback. The role checks use the normal UI. An absent UI control and an unchanged database demonstrate the tested scenario. Server enforcement is assessed below and in the prior API and requirement evidence. R04 covers unauthorized reads and writes. R07 covers write integrity. Risk register (team scope notes)

## Role and passive scan results

**7 selected Chromium cases passed, zero failed and zero skipped.** The run covers six authenticated access decisions across staff and customer roles, plus public intake. It includes allowed and denied paths with stored readback. [Pytest log](roles-pytest.txt) · [JUnit](roles-junit.xml)

**OWASP ZAP 2.17.0** ran in Safe mode through a loopback-only proxy using Java 21.0.11. The browser sent the normal form traffic from both workflows. ZAP recorded **207 requests**, including four form POSTs for intake, assignment, customer comment and staff resolution. The passive queue reached **zero**. ZAP logged **76 instances across nine rule IDs**. Six were medium, 46 low, and 24 informational, with zero high alerts. The API export retains methods, paths, status codes, rule IDs and risk ratings. Headers, bodies, cookies, query strings and alert payload evidence are omitted from the retained export. [Machine-readable ZAP report](zap-passive.json) · [Run log](zap-run.txt) · [ZAP passive scanner documentation](https://www.zaproxy.org/docs/desktop/addons/passive-scanner/)

To reproduce from a checkout of the tested candidate with the existing Python, Chromium and static vendor setup, copy the browser audit files there and use the [ZAP 2.17.0 cross-platform distribution](https://github.com/zaproxy/zaproxy/releases/tag/v2.17.0). Its ZIP SHA-256 matched `94c8f767b1c2e94f0db66b3ae56514d5e3f5a728ee1b6c798e0c8fe2d61fbff0`.

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$zapJar = 'C:\path\to\ZAP_2.17.0\zap-2.17.0.jar'
python tests/team/browser/zap_passive.py $zapJar .\zap-evidence-new
```

## Source review of two controls

1. **Role and object authorization (FR04–FR06/FR09, R04).** In the recorded candidate, the staff decorator (`decorators.py:18–23`) requires authenticated, active staff under the default policy. Staff detail calls the ticket permission guard before revealing the record (`views/staff.py:514`). The staff form update resolves objects in the user's authorized ticket queryset (`views/staff.py:1528`). Public receipt access checks the authenticated submitter identity or the ticket's receipt credential (`views/public.py:286`), and receipt follow-up retrieval filters private records. Verify that deployments retain the intended ticket scope and email ownership policy. [Historical role scenario results](../browser-2026-11-27/README.md#executed-results)
2. **CSRF coverage of ticket writes (FR07–FR11, R04/R07).** The local test settings include Django's CSRF middleware. Public intake, assignment, Respond and public comment forms emit CSRF tokens. Source review also identified a method-safety gap. The public receipt's resolution acceptance and staff's Take shortcut write during a GET, outside the CSRF middleware's unsafe-method checks. Neither path was probed for exploitation. [Middleware](../../../tests/test_settings.py) · [Intake form](../../../src/helpdesk/templates/helpdesk/public_create_ticket_base.html) · [Staff form](../../../src/helpdesk/templates/helpdesk/include/ticket_respond_form.html) · [Receipt](../../../src/helpdesk/templates/helpdesk/public_view_ticket.html)

## Dependency audit

`pip-audit 2.10.1` checked the project's existing `.venv/Lib/site-packages` using its PyPI advisory service. **49 installed packages, zero known advisories returned, zero skipped, exit 0.** The snapshot includes Django 6.1.3, django-helpdesk 2.6.1, Django REST Framework 3.18.3, Requests 2.34.2, urllib3 2.8.0, Pillow 13.0.0, Markdown 3.11.3, Beautiful Soup 4.15.0 and sqlparse 0.6.1. This check covers the installed Python environment. [Package and version inventory](pip-audit.json) · [Execution log](pip-audit.txt)

```powershell
uvx --from 'pip-audit==2.10.1' pip-audit --path .venv/Lib/site-packages --format json --output pip-audit.json
```

## Deduplicated findings and observations

| Finding | Component, impact and severity | Risk and recommended correction |
| --- | --- | --- |
| SEC-SRC-01 · Ticket state changes during GET | Public resolution acceptance and staff Take, Subscribe and Close actions write state on a read-style request. Link handling or unintended navigation in an authorized context can change a ticket. S2, source-confirmed method issue. | R04/R07. Use explicit POST actions protected by CSRF and keep existing role, ownership, transition and queue checks. Add regression checks that GET is read-only. [Staff and public source](#source-review-of-two-controls) |
| SEC-ZAP-01 · CSP missing · 10038 | Six HTML responses across public intake, login, staff detail and receipt lacked CSP. Reduced browser protection if unsafe script reaches a page. ZAP medium, S3 for the observed defense-in-depth gap. | R04. Design and test a CSP for the app and its script/style sources. Check it on the deployed responses. |
| SEC-ZAP-02 · Receipt data in URL · 10024 | Four receipt requests were flagged. The receipt source carries submitter identity and a receipt credential in the query string. Such URLs may persist in logs or history. ZAP informational, S3; deployment exposure remains unverified. | R04. Use narrowly scoped, expiring receipt credentials and avoid retaining them in query logs. Review referrer and logging policy. |
| SEC-ZAP-03 · Local response hardening · 10021, 10036 | Fourteen static assets lacked `X-Content-Type-Options`. Twenty-six responses disclosed server version information. These were observed on Django's local test server. ZAP low, S4 in this setup. | R04. Verify `nosniff` on production static responses and minimize detailed version information in the serving layer. |
| SEC-ZAP-04 · Cookie without HttpOnly · 10010 | Six responses triggered the rule. The test injects session cookies, and Django defaults to HttpOnly for session cookies but not CSRF cookies. The alert does not establish that a deployed authentication cookie lacks the flag. ZAP low; production applicability open. | R04. Review cookie roles and flags on a deployed login response. Preserve the application's CSRF behavior when setting flags. |
| SEC-ZAP-05 · Informational signals · 10027, 10031, 10109, 10112 | Static comments, low-confidence HTML attribute notices, and application/session detection. No confirmed script execution or secret disclosure from these signals. | R04. Review application-owned flagged rendering and comments. Maintain contextual escaping. Record application/session detection as inventory. |

The recommendations remain open. Source findings and passive observations are recorded without exploit reproduction. The loopback configuration and injected test sessions limit conclusions about production headers and login cookies.

AI use on 27 November 2026 included mapping the existing browser checks, reviewing the source controls, preparing the local passive run and classifying findings.
