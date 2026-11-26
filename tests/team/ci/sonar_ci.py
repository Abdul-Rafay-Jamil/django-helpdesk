"""Run and verify an isolated SonarQube Community Build quality gate."""

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

HOST = "http://127.0.0.1:9000"
GATE = "SQE candidate quality"
CONDITIONS = {"coverage": ("LT", "45"), "duplicated_lines_density": ("GT", "3")}
METRICS = "coverage,line_coverage,branch_coverage,lines_to_cover,uncovered_lines,duplicated_lines_density,ncloc"
AUTH = ""


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def api(endpoint, *, post=None, **query):
    url = HOST + "/api/" + endpoint
    if query:
        url += "?" + urlencode(query)
    headers = {"Authorization": f"Basic {AUTH}"} if AUTH else {}
    params = None if post is None else urlencode(post).encode()
    try:
        with urlopen(
            Request(url, data=params, headers=headers), timeout=12
        ) as response:
            raw = response.read()
    except HTTPError as exc:
        message = (
            exc.read().decode(errors="replace")
            if endpoint != "users/change_password"
            else ""
        )
        raise SystemExit(f"SonarQube {endpoint} HTTP {exc.code} {message}") from None
    return json.loads(raw) if raw else {}


def wait_for_server():
    for _ in range(60):
        try:
            with urlopen(HOST + "/api/system/status", timeout=12) as response:
                if json.load(response)["status"] == "UP":
                    return
        except (URLError, OSError):
            pass
        time.sleep(5)
    raise SystemExit("SonarQube did not become ready")


def scan(scanner, project, coverage, output, sha):
    global AUTH
    wait_for_server()
    AUTH = base64.b64encode(b"admin:admin").decode()
    password = secrets.token_urlsafe(32)
    api(
        "users/change_password",
        post={"login": "admin", "previousPassword": "admin", "password": password},
    )
    AUTH = base64.b64encode(f"admin:{password}".encode()).decode()
    api(
        "projects/create",
        post={"name": project, "project": project, "visibility": "private"},
    )
    api("qualitygates/create", post={"name": GATE})
    for metric, (operator, limit) in CONDITIONS.items():
        api(
            "qualitygates/create_condition",
            post={"gateName": GATE, "metric": metric, "op": operator, "error": limit},
        )
    api("qualitygates/select", post={"gateName": GATE, "projectKey": project})
    save(output / "policy.json", api("qualitygates/show", name=GATE))
    token = api(
        "user_tokens/generate",
        post={
            "name": "candidate-analysis",
            "type": "PROJECT_ANALYSIS_TOKEN",
            "projectKey": project,
        },
    )["token"]
    env = dict(os.environ, SONAR_TOKEN=token)
    # Community Build measures the overall candidate as one disposable project.
    # Avoid automatic pull-request parameter injection by the scanner.
    env.pop("GITHUB_ACTIONS", None)
    env.pop("GITHUB_EVENT_NAME", None)
    with (output / "scanner.log").open("w", encoding="utf-8") as log:
        subprocess.run(
            [
                scanner,
                f"-Dsonar.host.url={HOST}",
                f"-Dsonar.scm.revision={sha}",
                f"-Dsonar.python.coverage.reportPaths={coverage.as_posix()}",
                f"-Dsonar.scanner.metadataFilePath={(output / 'report-task.txt').as_posix()}",
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )


def verify(project, coverage, output):
    metadata = dict(
        row.split("=", 1)
        for row in (output / "report-task.txt").read_text().splitlines()
        if "=" in row
    )
    for _ in range(60):
        task = api("ce/task", id=metadata["ceTaskId"])["task"]
        if task["status"] not in {"PENDING", "IN_PROGRESS"}:
            break
        time.sleep(5)
    save(output / "compute-task.json", task)
    if task["status"] != "SUCCESS":
        raise SystemExit(f"SonarQube compute task status is {task['status']}")
    gate = api("qualitygates/project_status", analysisId=task["analysisId"])[
        "projectStatus"
    ]
    save(output / "gate.json", gate)
    measures = api("measures/component", component=project, metricKeys=METRICS)
    save(output / "measures.json", measures)
    values = {m["metric"]: m["value"] for m in measures["component"]["measures"]}
    xml = ElementTree.parse(coverage).getroot()
    lines, covered = int(xml.get("lines-valid")), int(xml.get("lines-covered"))
    actual = int(values["lines_to_cover"]), int(values["uncovered_lines"])
    save(
        output / "import.json",
        {
            "xml_sha256": hashlib.sha256(coverage.read_bytes()).hexdigest(),
            "xml_lines": lines,
            "xml_lines_covered": covered,
            "sonar_lines": actual[0],
            "sonar_lines_uncovered": actual[1],
        },
    )
    expected = {(metric, op, limit) for metric, (op, limit) in CONDITIONS.items()}
    applied = {
        (c["metricKey"], c["comparator"], c["errorThreshold"])
        for c in gate["conditions"]
    }
    if actual != (lines, lines - covered) or covered == 0 or applied != expected:
        raise SystemExit(
            "SonarQube coverage import or applied gate policy does not match the candidate"
        )
    print(
        f"SonarQube gate {gate['status']} | coverage {values['coverage']}% | duplication {values['duplicated_lines_density']}%"
    )
    if gate["status"] != "OK":
        raise SystemExit("SonarQube quality gate failed")


if __name__ == "__main__":
    scanner, source, destination = sys.argv[1:]
    coverage, output = Path(source), Path(destination)
    properties = dict(
        line.split("=", 1)
        for line in Path("sonar-project.properties").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    project = properties["sonar.projectKey"]
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if sha != os.environ["GITHUB_SHA"]:
        raise SystemExit("SonarQube checkout SHA differs from the coverage candidate")
    scan(scanner, project, coverage, output, sha)
    verify(project, coverage, output)
