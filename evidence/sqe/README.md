# SQE integration evidence

The integration starts at PR #5 head `bec9c208`. The artifacts below record the original
executions and their tested revisions.

| Suite | Evidence |
| --- | --- |
| API and generated boundaries | [Results, failed cases and regeneration evidence](api-2026-11-27/README.md) |
| Browser journeys | [Results, failed cases and usability observations](browser-2026-11-27/README.md) |
| Accessibility | [Original findings and intake landmark retest](accessibility-2026-11-27/README.md) |
| Security | [Role checks, passive scan and dependency audit](security-2026-11-27/README.md) |
| Performance and recovery | [k6 thresholds, recorded results and recovery cases](performance-recovery-2026-11-27/README.md) |
| Defects | [Register](defect-management-2026-11-27/register.md) and [root-cause analysis](defect-management-2026-11-27/root-cause.md) |

The [integration workflow](../../.github/workflows/sqe-integration.yml) runs the API,
component and recovery tests, browser journeys, accessibility audit, local passive
security scenarios and k6 performance workload. Known failures retain their original
assertions. Browser traces, scanner session data and bulky archives remain in the
team's local evidence store. Original failing pytest logs and JUnit reports are included.
Receipt URL values are redacted in the shared accessibility artifact.

The four mandatory checks remain in their existing workflows. See the
[requirement check](requirement-gate-2026-11-23/verified-check.json),
[mutation runs](mutation-gate-2026-11-23/verified-runs.json) and
[coverage and SonarQube runs](quality-gates-2026-11-27/executions.json).
The six demonstration PRs remain separate from the integration PR.

## Integration CI checklist

These results are from PR #26 head `c734648b` on 27 November 2026. They record
completed runs before the branch protection and release workflow update.

| Check | Result |
| --- | --- |
| [SQE Requirement Tests](https://github.com/Abdul-Rafay-Jamil/django-helpdesk/actions/runs/45984821922/job/140457610330) | Pass |
| [SQE Mutation Gate](https://github.com/Abdul-Rafay-Jamil/django-helpdesk/actions/runs/45984821909/job/140457610225) | Pass |
| [SQE Coverage Gate](https://github.com/Abdul-Rafay-Jamil/django-helpdesk/actions/runs/45984821849/job/140457609720) | Pass |
| [SQE SonarQube Gate](https://github.com/Abdul-Rafay-Jamil/django-helpdesk/actions/runs/45984821849/job/140457874823) | Pass |
| [SQE API, access and recovery](https://github.com/Abdul-Rafay-Jamil/django-helpdesk/actions/runs/45984821966/job/140457610783) | Fail. The 14 known API cases reproduced. |
| [SQE browser, accessibility and passive security](https://github.com/Abdul-Rafay-Jamil/django-helpdesk/actions/runs/45984821966/job/140457611271) | Fail. The three known ticket journey failures reproduced. |
| [SQE ticket-detail performance](https://github.com/Abdul-Rafay-Jamil/django-helpdesk/actions/runs/45984821966/job/140457611218) | Pass |
| [Upstream build matrix](https://github.com/Abdul-Rafay-Jamil/django-helpdesk/actions/runs/45984821967/job/140457611176) | Fail. The Python 3.15 / Django 6.1 job stopped in `ruff check` during `make checkformat`. The log reports C408 for unnecessary `dict()` calls in the accessibility audit test. |

The three integration jobs are required alongside the four existing checks on
`main`. The [release workflow](../../.github/workflows/release_to_pypi.yml) runs
the same integration suite on the selected candidate and waits for it before the
publish job can enter the protected release environment.
