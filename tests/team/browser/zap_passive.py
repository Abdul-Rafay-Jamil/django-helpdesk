"""Capture a local, passive ZAP report from existing browser scenarios."""

import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

CASES = (
    "uc01_intake[M]",
    "uc02_assign[M]",
    "uc02_assign[E1]",
    "uc03_followup[A2]",
    "uc04_resolve[M]",
    "uc04_resolve[E1]",
    "uc06_review[M]",
)


def wait_until(check, seconds, message):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(1)
    raise RuntimeError(message)


def capture(api, output):
    queue = "pscan/view/recordsToScan"
    wait_until(
        lambda: int(api(queue)["recordsToScan"]) == 0,
        30,
        "Passive scanner still has pending records after 30 seconds",
    )
    messages = api("core/view/messages", start=0, count=5000)["messages"]
    requests = []
    for item in messages:
        method, url, _ = item["requestHeader"].splitlines()[0].split(maxsplit=2)
        status = int(item["responseHeader"].splitlines()[0].split()[1])
        requests.append(
            {"method": method, "path": urlsplit(url).path, "status": status}
        )
    fields = ("pluginId", "alert", "risk", "confidence", "method", "messageId", "cweid")
    alerts = [
        {**{field: alert.get(field) for field in fields}, "path": urlsplit(alert["url"]).path}
        for alert in api("alert/view/alerts", start=0, count=5000)["alerts"]
    ]
    report = {
        "version": api("core/view/version")["version"],
        "mode": api("core/view/mode")["mode"],
        "recordsToScan": int(api(queue)["recordsToScan"]),
        "requests": requests,
        "alerts": alerts,
    }
    (output / "zap-passive.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    assert report["mode"].lower() == "safe"
    for pattern in (r"/tickets/submit/", r"/tickets/\d+/update/"):
        assert any(
            row["method"] == "POST" and re.fullmatch(pattern, row["path"])
            for row in requests
        ), f"No recorded workflow POST for {pattern}"
    print(f"ZAP recorded {len(requests)} requests and {len(alerts)} alert instances")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jar", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    proxy = f"http://127.0.0.1:{port}"
    key = secrets.token_hex(24)
    opener = build_opener(ProxyHandler({}))

    def api(path, **params):
        query = "?" + urlencode(params) if params else ""
        request = Request(f"{proxy}/JSON/{path}/{query}", headers={"X-ZAP-API-Key": key})
        with opener.open(request, timeout=4) as response:
            return json.load(response)

    with tempfile.TemporaryDirectory(prefix="sqe-zap-session-") as home:
        # Keep the disposable proxy database and full HTTP traffic outside the evidence.
        startup = Path(home) / "startup.txt"
        with startup.open("wb") as log:
            process = subprocess.Popen(
                [
                    "java", "-jar", str(args.jar.resolve()), "-daemon", "-silent",
                    "-dir", home, "-host", "127.0.0.1", "-port", str(port),
                    "-config", f"api.key={key}",
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                wait_until(
                    lambda: api("core/view/version").get("version"),
                    35,
                    "ZAP failed to start on loopback",
                )
                api("core/action/setMode", mode="safe")
                assert api("core/view/mode")["mode"].lower() == "safe"
                with (output / "roles-pytest.txt").open("wb") as results:
                    run = subprocess.run(
                        [
                            sys.executable, "-m", "pytest", "-v", "--tb=short",
                            "--ds=tests.test_settings",
                            f"--junitxml={output / 'roles-junit.xml'}",
                            *(f"tests/team/browser/test_ticket_journeys.py::test_{case}" for case in CASES),
                        ],
                        stdout=results,
                        stderr=subprocess.STDOUT,
                        env={
                            **os.environ,
                            "SQE_BROWSER_LOCAL_PROXY": proxy,
                            "SQE_BROWSER_EVIDENCE": "",
                        },
                        timeout=160,
                    )
                capture(api, output)
                return run.returncode
            finally:
                try:
                    api("core/action/shutdown")
                    process.wait(timeout=12)
                except (URLError, ConnectionError, TimeoutError, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait(timeout=5)
                log.flush()
                (output / "zap-startup.txt").write_bytes(
                    startup.read_bytes().replace(key.encode(), b"[redacted]")
                )


if __name__ == "__main__":
    raise SystemExit(main())
