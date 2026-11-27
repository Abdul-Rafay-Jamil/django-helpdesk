"""Generate focused, one-input-at-a-time cases from the Ticket Management contract."""

import argparse
import json
from pathlib import Path

SPEC = Path(__file__).with_name("openapi.json")
METHODS = {"get", "post", "patch", "put"}
INVALID_FORMAT = {
    "email": "not-an-email",
    "date-time": "not-a-date",
    "duration": "not-a-duration",
    "binary": "not-a-file",
}


def load_spec(path=SPEC):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve(spec, definition):
    while "$ref" in definition:
        ref = definition["$ref"]
        if not ref.startswith("#/"):
            raise ValueError(f"Expected a local OpenAPI reference: {ref}")
        definition = spec
        for part in ref[2:].split("/"):
            definition = definition[part.replace("~1", "/").replace("~0", "~")]
    return definition


def operations(spec):
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method in METHODS:
                yield path, method, operation


def sized_string(size, format_name):
    if format_name != "email":
        return "x" * size
    if size < 6:
        raise ValueError("Cannot make this length of email")
    local_size = min(64, size - 5)
    domain_size = size - local_size - 4
    labels = []
    while domain_size > 63:
        labels.append("b" * 60)
        domain_size -= 61
    labels.append("b" * domain_size)
    return "a" * local_size + "@" + ".".join(labels) + ".co"


def boundary_values(schema):
    kind = schema["type"]
    keywords = (
        ("minimum", "maximum")
        if kind == "integer"
        else ("minLength", "maxLength")
        if kind == "string"
        else ()
    )
    for key in keywords:
        if key not in schema:
            continue
        for delta, position in ((-1, "below"), (0, "at"), (1, "above")):
            size = schema[key] + delta
            if kind == "string" and size < 0:
                continue
            valid = (
                schema.get(keywords[0], float("-inf"))
                <= size
                <= schema.get(keywords[1], float("inf"))
            )
            if kind == "integer" and "enum" in schema:
                valid = valid and size in schema["enum"]
            value = (
                size if kind == "integer" else sized_string(size, schema.get("format"))
            )
            yield f"BVA-{key}-{position}", value, valid, None


def partitions(schema, location, required, form):
    if schema.get("readOnly"):
        yield (
            "EP-read-only-ignored",
            schema.get("example", "read-only input"),
            True,
            None,
        )
        return
    if location != "path":
        yield "EP-omitted", None, not required, None
    if not schema.get("x-sqe-explicit"):
        if "example" in schema:
            yield "EP-valid", schema["example"], True, None
        if location == "body" and not form:
            yield "EP-null", None, schema.get("nullable", False), None
            kind = schema["type"]
            yield "EP-wrong-type", {} if kind == "array" else [], False, None
        if (format_name := schema.get("format")) in INVALID_FORMAT:
            yield "EP-invalid-format", INVALID_FORMAT[format_name], False, None
        for value in schema.get("enum", []):
            valid = (
                schema.get("minimum", value) <= value <= schema.get("maximum", value)
            )
            yield f"EP-enum-{value}", value, valid, None
        yield from boundary_values(schema)
    for example in schema.get("x-sqe-cases", []):
        name = example["label"]
        label = name if name.startswith("BVA-") else f"EP-{name}"
        if name in {"below", "at", "above"}:
            label = f"BVA-page-{name}"
        yield label, example["value"], example["valid"], example.get("status")


def input_cases(operation, name, location, schema, required, form):
    unique = {}
    for label, value, valid, status in partitions(schema, location, required, form):
        omitted = label == "EP-omitted"
        if status is None and not valid:
            status = schema.get(
                "x-sqe-invalid-status", 404 if location == "path" else 400
            )
        key = (omitted, json.dumps(value, sort_keys=True), valid, status)
        if key in unique:
            unique[key]["partitions"].append(label)
            continue
        unique[key] = {
            "id": f"{operation}-{location}-{name}-{label}",
            "operation": operation,
            "location": location,
            "input": name,
            "partitions": [label],
            "omitted": omitted,
            "value": value,
            "valid": valid,
            "status": status,
        }
    return list(unique.values())


def generate(spec):
    result = []
    for _, _, operation in operations(spec):
        name = operation["operationId"]
        for reference in operation.get("parameters", []):
            parameter = resolve(spec, reference)
            result.extend(
                input_cases(
                    name,
                    parameter["name"],
                    parameter["in"],
                    resolve(spec, parameter["schema"]),
                    parameter.get("required", False),
                    False,
                )
            )
        if "requestBody" in operation:
            content = operation["requestBody"]["content"]
            form = "application/json" not in content
            preferred = "multipart/form-data" if form else "application/json"
            body = resolve(spec, content[preferred]["schema"])
            for field, definition in body["properties"].items():
                result.extend(
                    input_cases(
                        name,
                        field,
                        "body",
                        resolve(spec, definition),
                        field in body.get("required", []),
                        form,
                    )
                )
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--spec", type=Path, default=SPEC)
    cli.add_argument("--output", type=Path, required=True)
    options = cli.parse_args()
    rows = generate(load_spec(options.spec))
    options.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(rows)} focused cases in {options.output}")


if __name__ == "__main__":
    main()
