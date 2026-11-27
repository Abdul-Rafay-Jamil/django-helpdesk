# Section 3 · Ticket Management accessibility

Audited the same fixed candidate as the [six browser use cases](../browser-2026-11-27/README.md) on an isolated Django live server with synthetic SQLite records. The [revision record](revision.txt) lists the source SHA and original and improved template blob IDs in that order. The intake template is the only application change. [Patch](intake-landmark.patch)

## Screen and requirement traceability

| Use case and requirements | Audited key screen | Baseline reports |
| --- | --- | --- |
| UC01 / W1 / FR01–FR03 | Anonymous public intake with a queue accepting submissions | Lighthouse · [axe](before/intake.axe.json) |
| UC02 / W1 / FR04–FR05, FR10 | Authenticated staff Open ticket detail with the assignment control | Lighthouse · [axe](before/staff-open.axe.json) |
| UC03 / W2 / FR07, FR09–FR10 | Same Open ticket detail, with Respond expanded for the interactive form scan | Shared Lighthouse · [axe, Respond](before/staff-open-respond.axe.json) |
| UC04 / W2 / FR07–FR10 | Same Open ticket detail and Respond form, with resolution options | Shared Lighthouse · [axe, Respond](before/staff-open-respond.axe.json) |
| UC05 / W2 / FR08, FR10–FR11 | Staff Resolved detail with the resolution panel and close action | Lighthouse · [axe](before/staff-resolved.axe.json) |
| UC06 / W2 / FR06, FR09 | Authenticated customer opening the receipt with visible follow-up history | Lighthouse · [axe](before/customer-receipt.axe.json) |

The risk link for this phase is **R08**, inaccessible forms or feedback preventing task completion, in the team scope and risk register (team scope notes). Existing [keyboard, correction and resolution usability evidence](../browser-2026-11-27/README.md#usability-task-reuse) applies to the same workflows.

Lighthouse 13.6.0 used desktop navigation mode and enabled accessibility, best practices, and SEO. UC02–UC04 share one navigation URL. Node 24.15.0 ran Playwright 1.64.0 against the fixture's Chromium 156 binary. Playwright injected the full default axe rule set with `@axe-core/playwright` and axe-core 4.14.1. The Respond scan waits for the tab's transition to end before assessing its form. These are automated desktop checks of the selected states.

## Results

| Screen | Lighthouse accessibility | Best practices | SEO | axe violating rules |
| --- | ---: | ---: | ---: | ---: |
| Intake before | 95 | 100 | 100 | 4 |
| Staff Open | 83 | 100 | 91 | 8 |
| Staff Open, Respond tab | shared | shared | shared | 9 |
| Staff Resolved | 83 | 100 | 91 | 8 |
| Customer receipt | 86 | 100 | 100 | 6 |
| Intake after | 96 | 100 | 100 | 2 |

Scores are out of 100. Axe counts are rule counts per screen, and the same defect can appear on several screens. The Respond tab has its own full axe report because Lighthouse navigation opens the default tab. [Node and browser tooling](../../../tests/team/browser/audit/package.json) · [Baseline public and receipt log](before-pytest.txt) · [Baseline staff log](before-staff-pytest.txt) · [Expanded Respond log](before-respond-pytest.txt) · [Intake retest log](after-pytest.txt)

The first setup run encountered a hidden submit marker. The next run saved valid intake and receipt reports and reported an ambiguous staff marker. The targeted staff run used the assignment button as the marker and completed both staff reports. The initial Respond scan captured the tab's fade transition. The steady-state scan supplies the tab results above. [Setup log](setup-attempt-pytest.txt) · [Respond transition report](staff-open-respond-during-transition.axe.json)

The intake form now has a named `main` landmark. Screen-reader users can jump to the form and its instructions by landmark. Lighthouse changed `landmark-one-main` from fail to pass. Axe changed that rule to pass and stopped flagging the title and instructions as outside landmarks. The header list and missing level-one heading remain open. The intake still shows the original form with its Create Ticket button. Before Lighthouse · After Lighthouse · [Before axe](before/intake.axe.json) · [After axe](after/intake.axe.json) · [Before screenshot](before/intake.png) · [After screenshot](after/intake.png)

## Deduplicated findings and residual risk

All accessibility items trace to R08. Axe impact labels come from the reports.

| ID and affected element | User impact | Recommended correction and retest status |
| --- | --- | --- |
| A11Y-01 · Moderate · Pages lack a main landmark and place task content outside landmarks. `landmark-one-main` and `region`. | Screen-reader users must traverse navigation or guess where the form and ticket contents begin. | Add a named main landmark around task content and put standalone sidebar navigation in its own landmark. Intake passed both targeted retests. Staff detail and receipt remain open. |
| A11Y-02 · Critical · Staff assignment `select[name="owner"]` in the quick assignment control and Respond input `input[name="time_spent"]`. `select-name` and `label`. | Assistive technology cannot identify the quick owner picker or time input reliably. This impedes assigning tickets and recording work. | Associate the visible Assigned to label with a unique ID for the quick picker. Match the Time spent label's `for` value to the input ID. Open. |
| A11Y-03 · Critical · Staff sidebar button `#sidebarToggle`. `button-name`. | The icon-only control has no announced purpose. | Give the button an accessible name and expose its expansion state. Open. |
| A11Y-04 · Serious · Staff inactive ticket tabs and receipt Log out button. `color-contrast`. | Low-vision users may miss a tab or control. The measured ratios were 4.26:1 and 4.44:1 against 4.5:1. | Darken text against the actual backgrounds and verify stable tab states. Open. |
| A11Y-05 · Serious · Context help link in the reply instructions, including customer receipt. `link-in-text-block`. | The help link blends into the surrounding text for users who cannot distinguish colors. | Add a persistent underline or other non-color cue. Open. |
| A11Y-06 · Serious · Public header `.navbar-nav` contains a direct `div` child. Staff ticket related/dependency/attachment lists contain direct `small` children. `list`. | Assistive technology may report inaccurate list structure and item counts. | Use valid list-item wrappers or put empty-state text outside the list. The intake header issue remains after retest. |
| A11Y-07 · Moderate · Missing page-level heading and skipped levels under ticket title, resolution and follow-ups. `page-has-heading-one` and `heading-order`. | Heading navigation skips the page title or misrepresents the relationship between ticket sections. | Provide a task-level H1 and use a logical outline while retaining existing styling. Intake still has no H1. |
| SEO-01 · Staff CC Notifications disabled text rendered as `a.link-dark` without an href. `crawlable-anchors`. | A status label is rendered as a link despite having no destination. | Use ordinary text for the disabled status, with any explanation available without a hover dependency. Keep the staff detail authenticated. Open. |

Axe also requested review for unresolved `aria-describedby` references, gradient or image contrast, and a modal-trigger link. These are preserved in the raw `incomplete` arrays for follow-up. They are excluded from violation counts. Best practices scored 100 on all measured screens. No staff or receipt page was made public for indexing.

## Reproduce

Install the browser and static assets as in the [browser environment notes](../browser-2026-11-27/README.md). From the checkout to be audited, run this PowerShell command sequence in the existing Python environment.

```powershell
npm.cmd ci --prefix tests/team/browser/audit
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$env:SQE_ACCESSIBILITY_OUTPUT = (Join-Path (Get-Location) 'audit-artifacts')
python -m pytest -q -s --tb=short --ds=tests.test_settings tests/team/browser/test_accessibility_audits.py
```

Set `SQE_ACCESSIBILITY_SCREEN=intake` in the PowerShell environment to capture the one-screen retest in a fresh output folder. Keep the patched and original template runs separate. The [dependency lock](../../../tests/team/browser/audit/package-lock.json) pins the JavaScript tools. The harness uses loopback Chromium debugging and ephemeral fixture accounts.

AI use on 27 November 2026 included implementing the audit harness, checking raw results, adding the named landmark, and linking the findings to use cases and risk R08.
