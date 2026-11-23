# Ticket Management mutation validation

The production source is pinned to `e71b858b19eb9a7f0e19be9a9c2a80ac6b0cb83c`. The runner records the separate test commit and requires its entire `src` tree to match that baseline. The mutation cohort has 109 stock mutants in `update_ticket.py` and two custom mutants at the shared status guard and the staff Close target. The runner verifies the cohort ID checksum against the original 111. Ticket intake, public handlers, serializer internals and the rest of the staff module sit outside the mutation score.

The validation workflow runs on GitHub-hosted Ubuntu with Python 3.14.6. It installs the pinned runner and test dependency lists in separate virtual environments. From a clean checkout of this branch, reproduce it by creating a sandbox with a detached checkout of the test commit in its `control` subdirectory and running:

```text
<runner-python> tests/team/mutation/run_campaign.py --sandbox <sandbox> --output <new-evidence-dir> --test-python <test-python> --workers 3
```

Only after the unmutated 249-case control passes with all 77 requirements and zero skips does the campaign execute. Every worker starts from a clean detached checkout of the test commit. Cosmic Ray runs each test process against a fresh SQLite test schema without migrations and the script verifies source restoration. The raw denominator includes all 111 selected mutants. Timeout and invalid counts are separate. The validation job publishes the control JUnit, scope, version lists, summary and per-mutant evidence. It is branch-scoped and has no required-check configuration.
