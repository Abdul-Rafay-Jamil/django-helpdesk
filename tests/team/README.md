# SQE tests on the SQE-01 fix branch

From the repository root, install the project and the documented requirement workflow dependencies, plus coverage.py and the project test group. Run each suite with `python -m pytest --ds=tests.test_settings tests/team/<suite>`. The suites are `unit`, `component`, `requirements`, and `regression`.

For unit-only coverage, run `python -m coverage run --rcfile=tests/team/.coveragerc -m pytest --ds=tests.test_settings tests/team/unit`. Generate reports with the same coverage configuration. The application scope is `src/helpdesk` with migrations omitted.

## Test changes for the fork and SQE-01

- The two upstream email-notification scenarios set the ticket to Closed immediately before reopening it. That setup exercises a valid Closed to Reopened transition without adding notification traffic. The original recipient and count assertions still run.
- The 32-row staff matrix exercises the current `helpdesk_staff_member_required` wrapper. It reloads the decorator definition in an isolated namespace so the configured boolean policy is applied per case. Denied users redirect to login before the custom protector runs. For allowed users the custom protector can redirect before the view writes. Each row reloads the ticket and checks priority, history and the timestamp on rejected requests. The existing callable-policy cases remain active.
- The 12-row attachment matrix checks that extensionless and trailing-dot-only filenames are rejected when extension validation is enabled, regardless of the configured allowlist. It retains acceptance when validation is disabled and separately checks allowlisted and non-allowlisted extensions.
- All 77 requirement cases keep their original assertions. The three historical characterizations of accepted forbidden writes apply to the pre-fix baseline, which retains their test source and raw results. The active requirement and regression suites specify the corrected behavior.
