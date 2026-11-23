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

REVISION = "e71b858b19eb9a7f0e19be9a9c2a80ac6b0cb83c"
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
    if "SQE_PYTEST_EXIT=1" in lines and ("FAILED " in output or "ERROR " in output):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--test-python", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    assert version("cosmic-ray") == "8.9.0"
    control = args.sandbox.resolve() / "control"
    output = args.output.resolve()
    test_python = args.test_python.absolute()
    output.mkdir(parents=True, exist_ok=True)
    assert not any(output.glob("results/*.json"))
    test_revision = git(control, "rev-parse", "HEAD")
    assert git(control, "rev-parse", "HEAD:src") == git(
        control, "rev-parse", f"{REVISION}:src"
    )
    assert not git(control, "status", "--porcelain")
    changed_tests = git(
        control, "diff", "--name-only", REVISION, test_revision, "--", "tests"
    )
    assert set(changed_tests.splitlines()) - {
        "tests/team/unit/test_mutation_regressions.py"
    } <= {
        path
        for path in changed_tests.splitlines()
        if path.startswith("tests/team/mutation/")
    }
    output.joinpath("results").mkdir(exist_ok=True)

    runner = Path(__file__).with_name("run_tests.py").resolve()
    command = shlex.join([test_python.as_posix(), runner.as_posix(), *TESTS])
    save(
        output / "config.json",
        {
            "production_revision": REVISION,
            "production_tree": git(control, "rev-parse", f"{REVISION}:src"),
            "test_revision": test_revision,
            "cosmic_ray": version("cosmic-ray"),
            "test_command": command,
            "timeout_seconds": TIMEOUT,
            "workers": args.workers,
            "targets": list(TARGETS),
            "standard_operators_for_update_ticket": sorted(CORE),
            "custom_operators": sorted(OPERATORS),
        },
    )
    control_result = run_one(control, command, (), output / "control-junit.xml")
    save(output / "control.json", control_result)
    assert control_result["classification"] == "survived", control_result
    suite = ElementTree.parse(output / "control-junit.xml").find("testsuite")
    assert {
        key: suite.get(key) for key in ("tests", "failures", "errors", "skipped")
    } == {"tests": "249", "failures": "0", "errors": "0", "skipped": "0"}
    assert (
        len(
            [
                case
                for case in suite.iter("testcase")
                if case.get("classname").startswith("tests.team.requirements.")
            ]
        )
        == 77
    )
    print(f"Unmutated control passed in {control_result['elapsed_s']} s", flush=True)
    inventory = read_inventory(control, output / "discovered.sqlite")
    candidates = [
        mutation
        for mutation in inventory
        if (
            mutation.module_path.as_posix() == UPDATE
            and mutation.operator_name.removeprefix("core/") in CORE
        )
        or mutation.operator_name.startswith("sqe/")
    ]
    assert {
        m.operator_name for m in candidates if m.operator_name.startswith("sqe/")
    } == {f"sqe/{name}" for name in OPERATORS}
    assert sum(m.operator_name.startswith("sqe/") for m in candidates) == 2
    candidates.sort(
        key=lambda m: (
            not m.operator_name.startswith("sqe/"),
            m.module_path.as_posix(),
            m.start_pos,
            m.operator_name,
            m.occurrence,
        )
    )
    assert len(candidates) == 111
    selected_ids = sorted(mutation_id(m) for m in candidates)
    digest = hashlib.sha256("\n".join(selected_ids).encode()).hexdigest()
    assert (
        digest == "719f0e6ea7bf823786bdbe181d9ee04faf632366055fd9b4764bbffd33c15e1d"
    ), "Mutation cohort differs from the original 111"
    save(
        output / "inventory.json",
        {
            "discovered": len(inventory),
            "selected": len(candidates),
            "mutants": [{"id": mutation_id(m), **asdict(m)} for m in candidates],
        },
    )
    print(f"Selected {len(candidates)} of {len(inventory)} discovered", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        shards = [
            executor.submit(
                execute_shard,
                control.parent / f"worker-{i}",
                command,
                candidates[i :: args.workers],
                output / "results",
                test_revision,
            )
            for i in range(args.workers)
        ]
        for shard in shards:
            shard.result()
    outcomes = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in (output / "results").glob("*.json")
    }
    assert sorted(outcomes) == selected_ids
    counts = {label: 0 for label in ("killed", "survived", "timeout", "invalid")}
    for result in outcomes.values():
        counts[result["classification"]] += 1
    summary = {
        "production_revision": REVISION,
        "test_revision": test_revision,
        "discovered": len(inventory),
        "selected": len(candidates),
        "outside_scope": len(inventory) - len(candidates),
        "cohort_sha256": digest,
        "counts": counts,
        "raw_score": counts["killed"] / len(candidates),
        "custom_results_included": {
            m.operator_name: outcomes[mutation_id(m)]["classification"]
            for m in candidates
            if m.operator_name.startswith("sqe/")
        },
    }
    save(output / "summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
