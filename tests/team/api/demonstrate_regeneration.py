"""Demonstrate two changed constraints using a saved copy of the contract."""

import argparse
import hashlib
import json
from pathlib import Path

from generate_cases import SPEC, generate, load_spec


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def boundary(rows, operation, field, constraint):
    result = {}
    for row in rows:
        if row["operation"] == operation and row["input"] == field:
            for label in row["partitions"]:
                if label.startswith(f"BVA-{constraint}-"):
                    result[label.split("-")[-1]] = {
                        "value": len(row["value"])
                        if constraint == "maxLength"
                        else row["value"],
                        "valid": row["valid"],
                    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    original = SPEC.read_bytes()
    baseline = generate(load_spec())
    changed = load_spec()
    schemas = changed["components"]["schemas"]
    schemas["TicketTitle"]["maxLength"] = 80
    schemas["Priority"]["maximum"] = 4
    variant = args.output / "openapi-constraint-variant.json"
    write(variant, changed)
    modified = generate(load_spec(variant))
    assert SPEC.read_bytes() == original
    summary = {
        "original_spec_sha256": hashlib.sha256(original).hexdigest(),
        "variant_spec_sha256": hashlib.sha256(variant.read_bytes()).hexdigest(),
        "original_cases": len(baseline),
        "variant_cases": len(modified),
        "examples": {
            name: {
                "original": boundary(baseline, "createTicket", field, keyword),
                "changed": boundary(modified, "createTicket", field, keyword),
            }
            for name, field, keyword in (
                ("title maximum 200 to 80", "title", "maxLength"),
                ("priority maximum 5 to 4", "priority", "maximum"),
            )
        },
    }
    assert summary["examples"]["title maximum 200 to 80"]["changed"]["above"] == {
        "value": 81,
        "valid": False,
    }
    assert summary["examples"]["priority maximum 5 to 4"]["changed"]["above"] == {
        "value": 5,
        "valid": False,
    }
    write(args.output / "generated-original.json", baseline)
    write(args.output / "generated-variant.json", modified)
    write(args.output / "regeneration.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
