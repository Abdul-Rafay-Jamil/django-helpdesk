"""Invoke pytest for Cosmic Ray and retain its exit classification in stdout."""

import os
import sys
from pathlib import Path

import pytest

if __name__ == "__main__":
    sys.stderr = sys.stdout
    sys.path.insert(0, str(Path.cwd()))
    args = [
        "-p",
        "pytest_django.plugin",
        "--ds=tests.test_settings",
        "--nomigrations",
        "-q",
        "-x",
        *sys.argv[1:],
    ]
    if junit := os.environ.get("SQE_JUNIT"):
        args.append(f"--junitxml={junit}")
    outcome = pytest.main(args)
    print(f"SQE_PYTEST_EXIT={int(outcome)}", flush=True)
    raise SystemExit(outcome)
