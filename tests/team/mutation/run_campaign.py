"""Run a pinned Cosmic Ray campaign in detached Git clones."""

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from importlib.metadata import version
from pathlib import Path
from xml.etree import ElementTree

from attrs import asdict
from cosmic_ray import plugins
from cosmic_ray.commands import init
from cosmic_ray.mutating import mutate_and_test
from cosmic_ray.work_db import use_db
from cosmic_ray.work_item import TestOutcome, WorkerOutcome
from operators import OPERATORS

UPDATE = "src/helpdesk/update_ticket.py"
STAFF = "src/helpdesk/views/staff.py"
TARGETS = (UPDATE, STAFF)
CORE = {
    "NumberReplacer",
    "ReplaceAndWithOr",
    "ReplaceOrWithAnd",
    "ReplaceTrueWithFalse",
    "ReplaceFalseWithTrue",
    "ZeroIterationForLoop",
    "ReplaceComparisonOperator_Eq_NotEq",
    "ReplaceComparisonOperator_NotEq_Eq",
    "ReplaceComparisonOperator_Is_IsNot",
    "ReplaceComparisonOperator_IsNot_Is",
    "ReplaceComparisonOperator_Lt_LtE",
    "ReplaceComparisonOperator_Gt_GtE",
}
TESTS = (
    "tests/team/unit/test_mutation_regressions.py",
    "tests/team/unit/test_workflow_fields.py::test_update_records_metadata_custom_field_and_checklist_changes",
    "tests/team/unit/test_state_rule.py::test_resolution_decision_mcc",
    "tests/team/unit/test_staff_update.py",
    "tests/team/unit/test_staff_detail_actions.py",
    "tests/team/unit/test_workflow_notifications.py",
    "tests/team/unit/test_update_decisions.py",
    "tests/team/unit/test_workflow_recovery.py",
    "tests/team/unit/test_workflow_options.py",
    "tests/team/requirements",
    "tests/team/regression",
)
TIMEOUT = 45
MINIMUM_PERCENT = 95
CUSTOM_SITES = {
    "sqe/IgnoreConfiguredTransitionFlow": UPDATE,
    "sqe/CloseAsResolved": STAFF,
}
plugins.OPERATOR_PROVIDERS["sqe"] = OPERATORS


def save(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def git(checkout, *args):
    return subprocess.check_output(
        ["git", "-C", str(checkout), *args], text=True
    ).strip()


def clone(source, destination, test_revision):
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-checkout",
            "--shared",
            str(source),
            str(destination),
        ],
        check=True,
    )
    git(destination, "checkout", "--quiet", "--detach", test_revision)
    assert not git(destination, "status", "--porcelain")


def classify(result):
    if result.output == "timeout":
        return "timeout"
    if result.worker_outcome != WorkerOutcome.NORMAL:
        return "invalid"
    output = result.output or ""
    lines = output.splitlines()
    if "SQE_PYTEST_EXIT=1" in lines and any(
        line.startswith(("FAILED tests/", "ERROR tests/")) for line in lines
    ):
        return "killed"
    if result.test_outcome == TestOutcome.SURVIVED and "SQE_PYTEST_EXIT=0" in lines:
        return "survived"
    return "invalid"


def run_one(checkout, command, mutations, junit=None):
    os.chdir(checkout)
    os.environ["PYTHONPATH"] = str(checkout / "src")
    # Every worker has a fresh source tree. Cosmic Ray disables bytecode writes
    # while retaining the environment's dependency cache.
    os.environ.pop("PYTHONPYCACHEPREFIX", None)
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if junit:
        os.environ["SQE_JUNIT"] = str(junit)
    started = time.perf_counter()
    try:
        result = mutate_and_test(mutations, command, TIMEOUT)
    finally:
        os.environ.pop("SQE_JUNIT", None)
    return {
        "classification": classify(result),
        "elapsed_s": round(time.perf_counter() - started, 3),
        "cosmic_ray": asdict(result),
    }


def mutation_id(mutation):
    signature = f"{mutation.module_path.as_posix()}:{mutation.operator_name}:{mutation.occurrence}"
    return hashlib.sha256(signature.encode()).hexdigest()[:12]


def execute_shard(checkout, command, jobs, output, test_revision):
    clone(checkout.parent / "control", checkout, test_revision)
    hashes = {
        target: hashlib.sha256((checkout / target).read_bytes()).digest()
        for target in TARGETS
    }
    for job in jobs:
        result = run_one(checkout, command, (job,))
        assert all(
            hashlib.sha256((checkout / target).read_bytes()).digest() == digest
            for target, digest in hashes.items()
        )
        save(output / f"{mutation_id(job)}.json", {"mutation": asdict(job), **result})
    assert not git(checkout, "status", "--porcelain")


def read_inventory(control, database):
    os.chdir(control)
    with use_db(database) as session:
        init(tuple(Path(target) for target in TARGETS), session, {})
        return [item.mutations[0] for item in session.pending_work_items]


def inventory_digest(mutations):
    sites = [
        json.dumps(
            [
                m.module_path.as_posix(),
                m.operator_name,
                m.occurrence,
                m.start_pos,
                m.end_pos,
                m.definition_name,
                m.operator_args,
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
        for m in mutations
    ]
    return hashlib.sha256("\n".join(sorted(sites)).encode()).hexdigest()


def check_scope(control, output):
    inventory = read_inventory(control, output / "discovered.sqlite")
    candidates = [
        m
        for m in inventory
        if (
            m.module_path.as_posix() == UPDATE
            and m.operator_name.removeprefix("core/") in CORE
        )
        or m.operator_name.startswith("sqe/")
    ]
    candidates.sort(
        key=lambda m: (
            not m.operator_name.startswith("sqe/"),
            m.module_path.as_posix(),
            m.start_pos,
            m.operator_name,
            m.occurrence,
        )
    )
    observed = {
        "discovered": len(inventory),
        "selected": len(candidates),
        "discovered_sha256": inventory_digest(inventory),
        "selected_sha256": inventory_digest(candidates),
    }
    save(
        output / "inventory.json",
        {
            **observed,
            "mutants": [{"id": mutation_id(m), **asdict(m)} for m in candidates],
        },
    )
    manifest = json.loads(
        (control / "tests/team/mutation/scope.json").read_text(encoding="utf-8")
    )
    custom = {
        name: [m.module_path.as_posix() for m in candidates if m.operator_name == name]
        for name in CUSTOM_SITES
    }
    errors = []
    if custom != {name: [path] for name, path in CUSTOM_SITES.items()} or any(
        m.operator_name.startswith("sqe/") and m.operator_name not in CUSTOM_SITES
        for m in candidates
    ):
        errors.append(
            "Each required custom operator must generate exactly one mutant at its declared target."
        )
    if observed != manifest["inventory"] or not manifest["review"].strip():
        errors.append(
            "Mutation scope drift. Review the candidate inventory and update scope.json explicitly."
        )
    save(
        output / "scope-verification.json",
        {
            "reviewed": manifest,
            "observed": observed,
            "custom_sites": custom,
            "errors": errors,
        },
    )
    if errors:
        raise SystemExit(" ".join(errors))
    return candidates, observed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--test-python", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    assert version("cosmic-ray") == "8.9.0"
    control = args.sandbox.resolve() / "control"
    output = args.output.resolve()
    test_python = args.test_python.absolute()
    output.mkdir(parents=True, exist_ok=True)
    (output / "results").mkdir()

    candidate_sha = git(control, "rev-parse", "HEAD")
    if candidate_sha != args.candidate_sha or git(control, "status", "--porcelain"):
        raise SystemExit("Control checkout must be clean and match the candidate SHA.")
    if git(control, "rev-parse", f"{args.base_sha}^{{commit}}") != args.base_sha:
        raise SystemExit("Base SHA did not resolve to the expected commit.")
    changed = git(
        control, "diff", "--name-only", args.base_sha, candidate_sha, "--", "src"
    ).splitlines()
    outside = sorted(set(changed) - set(TARGETS))
    save(
        output / "production-scope.json",
        {
            "tested_sha": candidate_sha,
            "base_sha": args.base_sha,
            "changed_source_paths": changed,
            "changed_paths_outside_target_files": outside,
            "staff_scope": "Only the custom Close target is measured in views/staff.py.",
            "update_scope": "Only declared stock operator types and the custom transition guard are measured.",
        },
    )
    patch = git(
        control, "diff", "--unified=0", args.base_sha, candidate_sha, "--", "src"
    )
    (output / "source-changes.diff").write_text(patch + "\n", encoding="utf-8")
    print(
        f"Tested candidate {candidate_sha}; changed production paths outside target files: {outside}",
        flush=True,
    )
    runner = Path(__file__).with_name("run_tests.py").resolve()
    command = shlex.join([test_python.as_posix(), runner.as_posix(), *TESTS])
    save(
        output / "config.json",
        {
            "tested_sha": candidate_sha,
            "base_sha": args.base_sha,
            "production_tree": git(control, "rev-parse", "HEAD:src"),
            "cosmic_ray": version("cosmic-ray"),
            "test_command": command,
            "timeout_seconds": TIMEOUT,
            "workers": args.workers,
            "targets": list(TARGETS),
            "standard_operators_for_update_ticket": sorted(CORE),
            "custom_operators": CUSTOM_SITES,
            "minimum_percent": MINIMUM_PERCENT,
        },
    )

    result = run_one(control, command, (), output / "control-junit.xml")
    save(output / "control.json", result)
    if result["classification"] != "survived":
        raise SystemExit("Unmutated control failed. See control.json.")
    suite = ElementTree.parse(output / "control-junit.xml").find("testsuite")
    counts = {
        key: int(suite.get(key)) for key in ("tests", "failures", "errors", "skipped")
    }
    required = sum(
        c.get("classname", "").startswith("tests.team.requirements.")
        for c in suite.iter("testcase")
    )
    if required != 77 or any(counts[key] for key in ("failures", "errors", "skipped")):
        raise SystemExit(
            f"Unmutated control must execute all 77 requirements without failures or skips: {counts}"
        )
    print(
        f"Unmutated control passed {counts['tests']} cases ({required} requirements)",
        flush=True,
    )

    candidates, observed = check_scope(control, output)
    print(
        f"Selected {len(candidates)} of {observed['discovered']} discovered", flush=True
    )
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        shards = [
            executor.submit(
                execute_shard,
                control.parent / f"worker-{i}",
                command,
                candidates[i :: args.workers],
                output / "results",
                candidate_sha,
            )
            for i in range(args.workers)
        ]
        for shard in shards:
            shard.result()
    outcomes = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in (output / "results").glob("*.json")
    }
    if set(outcomes) != {mutation_id(m) for m in candidates}:
        raise SystemExit("Missing or unexpected per-mutant results.")
    counts = {label: 0 for label in ("killed", "survived", "timeout", "invalid")}
    for row in outcomes.values():
        counts[row["classification"]] += 1
    custom_results = {
        m.operator_name: outcomes[mutation_id(m)]["classification"]
        for m in candidates
        if m.operator_name in CUSTOM_SITES
    }
    errors = []
    if counts["killed"] * 100 < MINIMUM_PERCENT * len(candidates):
        errors.append(
            f"Raw mutation score below the {MINIMUM_PERCENT}% project threshold."
        )
    if any(result != "killed" for result in custom_results.values()):
        errors.append("Both custom mutants must be killed.")
    if counts["timeout"] or counts["invalid"]:
        errors.append("Timed-out and invalid mutants fail the gate.")
    summary = {
        "tested_sha": candidate_sha,
        "base_sha": args.base_sha,
        **observed,
        "outside_scope": observed["discovered"] - observed["selected"],
        "counts": counts,
        "raw_score": counts["killed"] / len(candidates),
        "minimum_percent": MINIMUM_PERCENT,
        "custom_results_included": custom_results,
        "gate": "failed" if errors else "passed",
        "errors": errors,
    }
    save(output / "summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)
    if errors:
        raise SystemExit(" ".join(errors))


if __name__ == "__main__":
    main()
