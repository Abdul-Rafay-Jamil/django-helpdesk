# Ticket Management API and generated data evidence

The final suite ran once on fixed PR #5 head `bec9c208546c127e0a36c344779113e9b4c8b601` in a detached checkout. The production tree matches the recorded fixed candidate and the added tests reside under `tests/team/api/`. The run used Django's isolated SQLite test database and the existing queue, staff, and ticket fixtures. [Environment](environment.txt) · [Case and build summary](summary.json)

The 11 selected operations cover customer form intake and customer record readback, staff ticket create/list/read/PATCH/PUT, and follow-up create/list/read. The OpenAPI 3.0.3 contract describes required, optional, nullable, read-only, and coercible inputs. It includes a synthetic varchar custom-field profile. Existing deviations are recorded alongside the intended schema. The generator emits one-input EP and below/at/above BVA for declared numeric and length limits. Explicit fixture cases supply relationship existence, pagination, and file-size boundaries. Optional values, Boolean and format choices, and unbounded values use EP wherever there is no additional defensible boundary. Shared authentication, CSRF, and media handling are tested independently of the field matrix. [OpenAPI specification](../../../tests/team/api/openapi.json) · [Generator](../../../tests/team/api/generate_cases.py) · [Generated cases](generated-original.json)

## Results

**589 executed, 575 passed, 14 failed, zero errors and zero skips.** The suite exited 1. It ran 561 generated HTTP cases across 87 operation/input pairs, 162 cases with a BVA label, and 28 integration cases. All 28 integration cases passed. The final suite retains the 14 genuine failures. [Raw pytest](api-pytest.txt) · [JUnit](api-junit.xml) · [Defect IDs and outcomes](summary.json) · [Targeted run](targeted-pytest.txt) · [Targeted boundaries](targeted-boundaries-pytest.txt)

The regeneration script copied the original contract, lowered the title maximum from 200 to 80, and lowered the priority maximum from 5 to 4. The title BVA moved from 199/200/201 to 79/80/81. The priority BVA moved from 4/5/6 to 3/4/5, with 5 now invalid. Deduplication yielded 561 original and 557 variant rows. Both specifications passed OpenAPI validation, and the original retained its SHA-256 checksum. The variant exercises the generator and leaves the deployed constraints intact. [Before/after data](regeneration.json) · [Variant spec](openapi-constraint-variant.json) · [Variant cases](generated-variant.json) · [OpenAPI validation](openapi-validation.txt)

## Confirmed defects

| ID | Route / condition | Expected and observed | Requirement | Proposed triage |
| --- | --- | --- | --- | --- |
| API-01 | Ticket POST omits `description` | Expected HTTP 400. Received 500 from direct access to the missing validated key. Ticket and related tables stayed unchanged. | FR01/FR10, R01/R07 | S2/P1. Validate required input before the create handler reads it. |
| API-02 | Follow-up POST supplies `comment: null` | Expected HTTP 201 for the accepted nullable schema. Received 500 when update logic called a string method on null. Ticket and history stayed unchanged. | FR07/FR10, R03/R07 | S2/P1. Normalize null or revise the contract to reject it at validation. |
| API-03 | Ticket POST, PATCH and PUT and follow-up POST use fractional relationship IDs | Expected HTTP 400 and unchanged records. Eleven cases received 200 or 201 with persisted effects. The tested fractions were coerced to an existing fixture ID. | FR01/FR04/FR07/FR10, R01/R03/R07 | S2/P1. Validate integer IDs before relation lookup. |
| API-04 | Customer form sends a text part as `attachment` | Expected a validation response and no ticket. The form returned 302 and created a ticket without an attachment. Genuine binary boundary cases passed. | FR01, R01 | S3/P2. Validate malformed or unsupported attachment input explicitly. |

Other observed behavior is recorded separately from the failed cases. The status filter ignores unknown tokens and can return an unfiltered authorized list. Ticket creation accepts but ignores `resolution`, `on_hold`, and `merged_to`. A file accepted by Ticket PATCH or PUT creates no attachment row. Direct Ticket PATCH and PUT status changes create no audit note. A repeated follow-up POST adds its own note each time, while the configured status change produces a single audit event. The retry oracle applies to state-setting effects under scope assumption A2. [Generated assertions](../../../tests/team/api/test_generated_inputs.py) · [Integration assertions](../../../tests/team/api/test_workflows.py)

## Requirement traceability

| Requirements | Executable evidence and outcome |
| --- | --- |
| FR01–FR03, intake and priority | `submitPublic` and `createTicket` generated families verify the stored queue, details, initial Open status, and opening note. Priority EP/BVA passes including database readback. API-01, API-03 and API-04 are recorded above. |
| FR04–FR06, assignment, role rejection and record retrieval | `test_customer_intake_staff_assignment_private_resolution_and_readback` follows one ID from customer intake to staff assignment and customer readback. Customer assignment is denied without writes. Staff assignment persists. Detail/list input cases cover lookup and pagination. API-03 remains open. |
| FR07–FR08, follow-up and state flow | `createFollowUp` and `getFollowUp` verify linked history. All 25 `test_put_transition_and_idempotent_retry` rows pass, with 15 allowed and 10 denied. Earlier PATCH and follow-up POST transition results are reused because the production tree is unchanged. API-02 and API-03 remain open. [Earlier requirement evidence](../mutation-hosted-2026-11-23/raw/control-junit.xml) |
| FR09–FR10, authorization and rejection integrity | `test_transport_authentication_and_invalid_requests` and the customer journey verify denial and private projection. Generated rejected cases compare ticket, follow-up, audit, attachment and custom-value state. The 14 failed cases are preserved above. |
| FR11–FR12, retry and recovery | `test_put_transition_and_idempotent_retry`, the customer journey, and `test_api_failure_before_write_and_retry` verify unchanged identity, no duplicate transition events, and one recovered update after an injected pre-write failure. |

Run from the fixed checkout with the same test dependencies as PR #5. Set `PYTHONPATH` to that checkout's `src`.

```text
python tests/team/api/demonstrate_regeneration.py <evidence-dir>
python -m pytest -q --tb=short --ds=tests.test_settings --junitxml=<evidence-dir>/api-junit.xml tests/team/api
```

The four defects need triage and later regression verification. Deployment-specific custom fields, broader file handling, and operations outside the two workflows remain outside this suite. For the viva, capture the regeneration before/after and the final result summary with a representative endpoint failure. AI use on 27 November 2026 included drafting the contract, generator and tests, checking serializer behavior, and reconciling generated IDs with the final JUnit report.
