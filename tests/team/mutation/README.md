# Ticket Management mutation gate

The GitHub Actions check is named **SQE Mutation Gate**. On a PR targeting main it checks out GitHub's merge candidate and records the merge, head and base SHAs. The unmutated control and every Cosmic Ray worker execute against clean detached checkouts of that merge candidate. The control must pass with all 77 requirement cases and zero skips before mutant generation. The runner starts each invocation with a fresh result directory.

The scope is Ticket Management's shared update module, with 12 named stock operator types and the custom configured transition guard, plus the custom staff detail Close target. The initial cohort contains 109 stock and two custom mutants. The workflow records the full PR production diff and explicitly lists changed source files outside the target files. In the staff module only the Close target is measured. Intake, public view handlers and serializer internals have no score under this scope.

The committed scope.json records reviewed candidate counts and fingerprints for both the full discovered inventory and the selected cohort. They include paths, operators, occurrence, function and source positions. A mismatch fails after publishing the observed inventory. Review changes to production scope and operators in the PR, update the manifest with the observed counts and fingerprints only after reviewing the sites, and document the rationale in its review field. Both custom operators must generate exactly one site at their declared module, independently of the manifest.

The project policy is **95% raw mutation score** across all selected mutants, with both custom mutants killed and zero timeouts or invalids. With the original 111 this requires at least 106 kills. The assignment calls for a justified threshold and does not specify 95%. Failures preserve the available test, inventory and mutant evidence as a workflow artifact. This scope review and score must be revisited when the business rules or their selected modules change.

To run locally, install the pinned tool and test dependency lists in separate Python 3.14.6 environments. Put a clean detached checkout of the candidate in the sandbox's control directory. Run:

```text
<runner-python> tests/team/mutation/run_campaign.py --sandbox <sandbox> --output <new-evidence-dir> --test-python <test-python> --candidate-sha <candidate-sha> --base-sha <base-sha> --workers 3
```
