# Ticket Management use cases and browser evidence

The six use cases are allocated two per team member. Each row maps to one parameterized Chromium test in [test_ticket_journeys.py](../../../tests/team/browser/test_ticket_journeys.py). M is the main flow, A1 and A2 are alternatives, and E1 and E2 are exceptions. A visible outcome and a stored postcondition apply to each row. Django supplies the existing synthetic staff, queue, and ticket fixtures. Each browser session starts with a fresh context.

## UC01 · Saim · Submit a request (W1, FR01–FR03)

Precondition. A customer can open the public home page and at least one queue accepts submissions. Start with no tickets. Postcondition. A successful submission creates one Open ticket and one opening follow-up. A blocked submission leaves the store empty.

| Flow | Actions | Visible outcome | Stored postcondition |
| --- | --- | --- | --- |
| M | Open Create a ticket. Starting at Queue, use the keyboard to select it, enter title and description, move through Priority, Due on, Attach File, and email, then press Enter on Create Ticket. | Focus follows that order. The detail page shows the submitted title, description and Open status. | One ticket with default priority and the selected queue, with one matching opening note. |
| A1 | Select priority 2 and submit valid details. | The new ticket is shown as Open with the submitted title and description. | One ticket with priority 2 and one opening note. |
| A2 | Select the second public queue and submit valid details. | The created ticket detail is shown. | The second queue owns the new Open ticket and opening note. |
| E1 | Try to submit with the description empty. Read the required-field feedback, fill the description, and submit again. | Chromium focuses the missing field and presents corrective feedback. The form retains the title. After correction, the ticket detail appears. | Zero tickets after the rejected attempt. One Open ticket with the corrected description and one opening note after retry. |
| E2 | Disconnect the submission request. Reopen the form once the request aborts. | No ticket confirmation appears. The Create Ticket form is available again. | Zero tickets and zero opening notes. |

## UC02 · Saim · Assign or reassign a request (W1, FR04–FR05, FR10)

Precondition. A ticket is Open. Staff can access its queue and an eligible assignee exists. The requesting customer has a separate account. Postcondition. An accepted assignment changes the same ticket and writes one Owner audit record. A denied action preserves the ticket and its history.

| Flow | Actions | Visible outcome | Stored postcondition |
| --- | --- | --- | --- |
| M | An authorized staff member opens an unassigned ticket, selects another agent in the assignment control, and saves. | Ticket detail shows the selected agent. | The original Open ticket is assigned to that agent with one Owner change. |
| A1 | Use the Take shortcut on the unassigned ticket. | Ticket detail shows the signed-in staff member as owner. | The same ticket is assigned to that user with one Owner change. |
| A2 | Replace the existing staff owner with another eligible agent and save. | The selection shows the new owner. | One Owner change replaces the prior assignee on the existing ticket. |
| E1 | While signed in as a customer, open the staff detail URL. | Staff detail and its assignment control are denied. | Ownership, status and history remain unchanged. |
| E2 | Select an agent who becomes unavailable after the form opens. Save the stale selection. | A useful error identifies the rejected owner. | Ticket, timestamp and audit history remain unchanged. |

## UC03 · Hassan · Add a follow-up (W2, FR07, FR09–FR10)

Precondition. An Open ticket with an opening note exists. Staff can open its detail page and the customer can locate their public copy. Postcondition. A successful reply creates one linked follow-up with the selected visibility. A denied route preserves prior state.

| Flow | Actions | Visible outcome | Stored postcondition |
| --- | --- | --- | --- |
| M | Staff opens Respond, fills a comment and saves the default public reply. | The exact entered comment appears once in follow-ups. | One new public note is attributed to staff on the same Open ticket. |
| A1 | Staff switches visibility to private, enters a comment, and saves. | The internal reply appears once in staff history. | One new private note is attributed to staff. |
| A2 | The signed-in customer opens the ticket receipt link, enters a public comment, and clicks Update This Ticket. | The public history shows the comment once. | One public note is linked to the same Open ticket. |
| E1 | An anonymous visitor opens the staff detail link. | The login page appears and the Respond form is unavailable. | Existing ticket and history remain unchanged. |
| E2 | Staff opens a stale detail link for a missing ticket. | HTTP 404 and the Not Found heading are visible. | An existing comparison ticket and its history remain unchanged. |

## UC04 · Hassan · Resolve a ticket (W2, FR07–FR10)

Precondition. The ticket is in the flow-specific status and staff can respond. The dependency case has a separate outstanding Open ticket. Postcondition. An allowed resolve action stores the resolution and one follow-up. A status change writes one audit entry. A denied UI path leaves the initial state intact.

| Flow | Actions | Visible outcome | Stored postcondition |
| --- | --- | --- | --- |
| M | From Open, enter a resolution, select Resolved and save. | Status changes to Resolved. The resolution and new follow-up are both visible. | The same ticket stores the exact resolution and one status audit. |
| A1 | Resolve a Reopened ticket through Respond. | Resolved status, new resolution and follow-up are visible. | The Reopened ticket becomes Resolved with one status audit. |
| A2 | Edit the resolution text while retaining Resolved and save. | The new resolution is displayed and a follow-up appears. | One note is added while no duplicate status audit is written. |
| E1 | The customer opens their Open ticket receipt and looks for the Resolved option. | The public page displays Open and offers no Resolve control in that state. | No changes to ticket or history. |
| E2 | Staff opens Respond for a ticket with an outstanding dependency. | The Resolve radio is disabled and the page explains the dependency. | No status, resolution or history changes. |

## UC05 · Rafay · Close or reopen (W2, FR08, FR10–FR11)

Precondition. The ticket already has a resolution and is in the stated source status. Staff can open its detail. Postcondition. An allowed transition keeps the prior resolution, adds one follow-up and writes one status audit. A denied attempt preserves the current authoritative state.

| Flow | Actions | Visible outcome | Stored postcondition |
| --- | --- | --- | --- |
| M | On a Resolved ticket, click Accept and Close. | Status shows Closed. | The same ticket becomes Closed with one audit and retains the resolution. |
| A1 | Open Respond on a Resolved ticket and submit Reopened with a note. | Reopened status and the note appear. | The same ticket becomes Reopened with one audit and retains the resolution. |
| A2 | Open Respond on a Closed ticket and submit Reopened with a note. | Reopened status and the note appear. | The Closed ticket becomes Reopened with one audit and retains the resolution. |
| E1 | View permitted choices for a Closed ticket. | Resolved is absent, while Reopened is offered. | Closed status, resolution and history remain unchanged. |
| E2 | Open Respond on a Resolved ticket in tab A. Close it in tab B, then submit tab A's stale Resolved choice with a comment. | Tab B shows Closed. Tab A reports that the status change is forbidden. | Only tab B's close is persisted. The stale comment and second transition are rejected. |

## UC06 · Rafay · Review the outcome (W2, FR06, FR09)

Precondition. A customer owns a ticket with one public and one staff-private note. The main flow uses the issued receipt link under default settings. For the other flows, the fixture enables the optional public search form, where a valid lookup uses the ticket ID and the customer's authenticated email. Postcondition. Lookup leaves records unchanged and displays only the permitted view.

| Flow | Actions | Visible outcome | Stored postcondition |
| --- | --- | --- | --- |
| M | Open the issued receipt link for the customer's Open ticket. | The result shows the exact ticket, Open status and public note. | Ticket and both notes are unchanged. |
| A1 | Search for a Resolved ticket with its ID and matching email. | The result shows Resolved and its resolution. Only the public note appears. | Ticket, resolution and history are unchanged. |
| A2 | Search for a Closed ticket with the matching email. | Closed status and the resolution appear. The private note is absent. | Ticket, resolution and history are unchanged. |
| E1 | Search with a missing ticket ID and the matched account email. | The form explains that the ticket ID or email is invalid. | The existing ticket is unchanged and no new row appears. |
| E2 | An unrelated signed-in customer searches the live ID using their own email. | The same validation feedback appears without ticket details. | The target ticket and its history remain unchanged. |

## Usability task reuse

UC01-M verifies the keyboard task for W1, including focus order and Enter submission. UC01-E1 verifies useful feedback and correction within the same W1 task. UC04-M is the W2 task and verifies visible resolution confirmation and persisted status/history. These flows are part of the same 30-case suite.

## Executed results

On the fixed candidate `bec9c208546c127e0a36c344779113e9b4c8b601`, Playwright 1.64.0 with Chromium 156.0.8078.4 executed **30 scenarios**. **27 passed, three failed, zero errors and zero skips.** Both usability tasks passed. The Chromium validation message was “Please fill out this field.” It is recorded in JUnit alongside the browser version. [Raw pytest](browser-pytest.txt) · [JUnit](browser-junit.xml) · [Per-flow results and tested file hashes](summary.json) · [Environment](environment.txt)

| Use case | Passed | Failed |
| --- | --- | --- |
| UC01 | M, A1, A2, E1, E2 | — |
| UC02 | M, A1, A2, E1 | E2 |
| UC03 | M, A1, A2, E1, E2 | — |
| UC04 | M, A1, A2, E1, E2 | — |
| UC05 | M, A1, A2, E1, E2 | — |
| UC06 | M, A1, A2 | E1, E2 |

| Defect | Actual result and persisted state | Trace and requirement |
| --- | --- | --- |
| UI-01, stale assignee | Saving an agent removed after the form opened returned HTTP 500. The existing ticket, timestamp and history stayed unchanged. Staff update resolves the posted owner ID with an uncaught lookup. Expected feedback should identify the unavailable owner. | FR04/FR10. UC02-E2 trace (see the [original pytest log](browser-pytest.txt)) |
| UI-02, missing lookup feedback | With public search enabled, a missing ID and an unrelated customer's lookup both returned the search form without the intended error message. The ticket was withheld and stored state stayed unchanged. The lookup passes its message as a positional argument, while the receiving context reads a keyword argument. | FR06/FR09. UC06-E1 trace (see the [original pytest log](browser-pytest.txt)) · UC06-E2 trace (see the [original pytest log](browser-pytest.txt)) |

The targeted runs retained their original results. The first isolated a tooltip selector that needed the rendered accessible name and established that public lookup requires an opt-in fixture. The second confirmed the two product defects. The full suite ran once after those test adjustments. [First targeted run](targeted-pytest.txt) · [Second targeted run](targeted-followup-pytest.txt)

## Reproduction and evidence

Use the fixed checkout, set `PYTHONPATH` to its `src` and set `SQE_BROWSER_EVIDENCE` to a fresh evidence directory. Install the recorded Playwright version and Chromium. Build static vendor assets through the existing project setup or reuse the assets with the matching package and lock files. This run reused the existing built assets after confirming the dependency manifests matched.

```text
python -m playwright install chromium
python -m pytest -v --tb=short -o junit_family=legacy --ds=tests.test_settings --junitxml=<evidence-dir>/browser-junit.xml tests/team/browser
```

Submission screenshots are captured in the final run. Chromium's native validation bubble is recorded as text in JUnit. [Keyboard focus on submit](final/UC01-M-keyboard-submit.png) · [Invalid field focus](final/UC01-E1-correction-focus.png) · [Successful resubmission](final/UC01-E1-corrected-success.png) · [Resolution confirmation](final/UC04-M-resolved-confirmation.png)

The tracked application source in this browser run matches the fixed revision. UI-01 and UI-02 await triage. The subsequent audits reuse the six use cases and document the intake improvement and R08 findings. [Lighthouse and axe-core evidence](../accessibility-2026-11-27/README.md)

AI use on 27 November 2026 included drafting the use cases and browser fixtures, adapting selectors to the rendered UI, and verifying the evidence against pytest's final JUnit report.
