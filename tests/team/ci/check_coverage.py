"""Enforce measured statement and branch floors for the PR candidate."""

import json
import sys
from pathlib import Path
from xml.etree import ElementTree

output = Path(sys.argv[1])
suite = ElementTree.parse(output / "junit.xml").find("testsuite")
if int(suite.get("tests", 0)) < 1 or any(
    int(suite.get(key, 0)) for key in ("failures", "errors", "skipped")
):
    raise SystemExit("The unit suite must execute without failures, errors or skips.")

reports = {"application": "coverage.json", "ticket_management": "workflow.json"}
floors = {"application": (54, 36), "ticket_management": (85, 82)}
summary = {"unit_cases": int(suite.get("tests")), "scopes": {}}
failed = []
for scope, report in reports.items():
    totals = json.loads((output / report).read_text(encoding="utf-8"))["totals"]
    values = summary["scopes"][scope] = {}
    for name, count, total, floor in zip(
        ("statements", "branches"),
        ("covered_lines", "covered_branches"),
        ("num_statements", "num_branches"),
        floors[scope],
        strict=True,
    ):
        covered, possible = totals[count], totals[total]
        passed = 0 < possible and covered * 100 >= floor * possible
        values[name] = {
            "covered": covered,
            "total": possible,
            "minimum_percent": floor,
            "passed": passed,
        }
        print(f"{scope} {name}: {covered}/{possible} (minimum {floor}%)")
        if not passed:
            failed.append(f"{scope} {name}")
summary["status"] = "failed" if failed else "passed"
(output / "summary.json").write_text(
    json.dumps(summary, indent=2) + "\n", encoding="utf-8"
)
if failed:
    raise SystemExit("Coverage below project floors for " + ", ".join(failed))
