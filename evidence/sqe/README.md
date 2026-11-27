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
