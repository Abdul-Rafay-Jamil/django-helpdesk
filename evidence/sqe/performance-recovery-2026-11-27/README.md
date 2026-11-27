# Section 5 · Performance and recovery

## Performance method

The selected operation is authenticated staff ticket-detail retrieval, `GET /api/tickets/{id}/`. The same ticket is read back during assignment and resolution verification. The [pre-execution plan](test-plan.md) retains EC3's **p95 ≤ 500 ms** and **unexpected failures < 1%** for each run. The workload allows one second of think time between iterations. Throughput is reported as an observation.

All runs use the same Django live server and synthetic SQLite ticket with an opening follow-up. The harness uses the existing staff, queue and ticket fixtures. It checks the business-record seed before and after each run. The test runner and load generator run on the same machine. [Environment and build](environment.txt) · [k6 script](../../../tests/team/performance/ticket_detail.js) · [Fixture harness](../../../tests/team/performance/run_workload.py)

## Performance results

Every 65-second scenario recorded approximately 64.00 seconds between the first and last samples at its target VU count. All four runs met both pre-set limits. The seed fingerprint and record counts stayed identical across every before/after check. [Raw seed and exit records](run-manifest.json) · [Machine-readable comparison](comparison.json) · [Harness JUnit](performance-junit.xml)

| Run | Attempts | Requests/s | p95 | Unexpected failures | Raw evidence |
| --- | ---: | ---: | ---: | ---: | --- |
| 1 VU · 1 | 64 | 0.975 | 48.78 ms | 0/64, 0.00% | [Summary](1vu-run1.summary.json) · · |
| 1 VU · 2 | 64 | 0.975 | 49.93 ms | 0/64, 0.00% | [Summary](1vu-run2.summary.json) · · |
| 5 VUs · 1 | 320 | 4.855 | 46.71 ms | 0/320, 0.00% | [Summary](5vu-run1.summary.json) · · |
| 5 VUs · 2 | 310 | 4.727 | 86.31 ms | 0/310, 0.00% | [Summary](5vu-run2.summary.json) · · |

The second 5-VU run had the highest tail latency and slightly lower throughput, while meeting EC3. The four runs produced 758 successful retrievals. Risk R06 is addressed for this local operation and two tested workloads. Production performance remains unmeasured. [Threshold semantics](https://grafana.com/docs/k6/latest/using-k6/thresholds/)

## Recovery evidence reused

| FR12 criterion | Existing evidence reviewed |
| --- | --- |
| Controlled failure | The API test injects a deterministic failure at `Ticket.save` during the authenticated follow-up operation and observes HTTP 500. The unit case injects the same failure at the public transition path. |
| Persisted state after failure | The API case compares the saved snapshot before and after failure. The unit case refreshes the record and checks status, resolution, modified time and follow-up IDs. |
| Restoration and retry | The scoped patch exits before retry. The API uses the same request payload, gets HTTP 201 and checks the persisted status, follow-up ID and status audit. The unit case also replays the restored request a second time. |
| Duplicate prevention | After failure and two submissions with the restored dependency, the unit case verifies one ticket, one follow-up and one status audit. The API case verifies a single ticket and follow-up after retry. |

Both named cases passed in their saved executions. The HTTP evidence is from the fixed candidate used for the performance test, before the intake-template change. The duplicate-on-replay assertion is from validation revision `5aad5301a1638817d93b0078953b2c7459c99f2e`. Together the evidence covers FR12 and risks R05 and R07 within the controlled pre-write fault boundary. [API case](../../../tests/team/api/test_workflows.py) · [API JUnit](../api-2026-11-27/api-junit.xml) · [Replay unit case](../../../tests/team/unit/test_workflow_recovery.py) · [Unit JUnit](../sqe-01-lint-validation-2026-11-23/unit-junit.xml) · [Unit revision and environment](../sqe-01-lint-validation-2026-11-23/local-manifest.json) · Risk register (team scope notes)

## Reproduction

Use the fixed candidate and existing Python environment. The workload harness creates its own Django test database and staff session. Download the Windows archive from the [official k6 v2.0.0 release](https://github.com/grafana/k6/releases/tag/v2.0.0) and compare the archive SHA-256 with the environment record.

```powershell
$env:SQE_K6_EXE = 'C:\path\to\k6.exe'
$env:SQE_K6_OUTPUT = (Join-Path (Get-Location) 'k6-evidence-new')
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m pytest -q --tb=short --ds=tests.test_settings tests/team/performance/run_workload.py
```

One preparatory check initially expected a status label while the staff endpoint returns a numeric status. It stopped before sending any k6 traffic. The saved [preflight failure](preflight-pytest.txt) documents the correction to the harness. The thresholds were established before that check and remained unchanged.

AI use on 27 November 2026 included preparing the k6 workload and seed checks, reviewing saved recovery assertions, and analyzing the raw metrics.
