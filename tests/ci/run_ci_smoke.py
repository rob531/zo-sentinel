#!/usr/bin/env python3
"""
run_ci_smoke.py -- entrypoint for the hermetic CI smoke ladder.

Wires the runtime context the ladder needs, then runs it:

    1. start tests/mock_write_service.py on an ephemeral port
    2. wait for its /health
    3. export ZO_WRITE_SERVICE + GATE_ERRORS_DB so the parametrized gate
       framework + bootstrap target the mock / a temp DuckDB (never the host)
    4. run the recursive ladder (tests.ci.smoke_ladder)
    5. write junit XML (consumed by gh_actions_fetcher.py)
    6. tear the mock down, exit 0 (all pass) / 1 (failure) / 2 (harness error)

Run from the repo root:

    python -m tests.ci.run_ci_smoke

Env knobs:
    CI_SMOKE_WS_PORT   mock write_service port           (default 8772)
    CI_SMOKE_JUNIT     junit output path                 (default artifacts/ci_smoke_junit.xml)
    GATE_ERRORS_DB     ephemeral gate_errors db path      (default <tmp>/gate_errors.db)
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _free_port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_health(url: str, timeout_s: float = 30.0) -> bool:
    import requests
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = requests.get(url + "/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def main() -> int:
    port = int(os.environ.get("CI_SMOKE_WS_PORT", "8772"))
    ws_url = f"http://127.0.0.1:{port}"
    junit_path = Path(os.environ.get(
        "CI_SMOKE_JUNIT", str(REPO_ROOT / "artifacts" / "ci_smoke_junit.xml")))

    # Ephemeral gate_errors db path so any code that honours GATE_ERRORS_DB
    # (gate_framework, gate_errors_bootstrap) can never touch the host file.
    if "GATE_ERRORS_DB" not in os.environ:
        os.environ["GATE_ERRORS_DB"] = str(
            Path(tempfile.gettempdir()) / "ci_gate_errors.db")

    # Export BEFORE importing the ladder: gate_framework reads ZO_WRITE_SERVICE
    # at import time, and tier3 asserts the override took effect.
    os.environ["ZO_WRITE_SERVICE"] = ws_url

    mock_script = REPO_ROOT / "tests" / "mock_write_service.py"
    if not mock_script.exists():
        print(f"FATAL: mock not found at {mock_script}", file=sys.stderr)
        return 2

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    print(f"[harness] starting mock write_service on :{port}")
    proc = subprocess.Popen(
        [sys.executable, str(mock_script), "--port", str(port)],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_health(ws_url, timeout_s=30):
            print("FATAL: mock write_service did not become healthy in 30s",
                  file=sys.stderr)
            return 2
        print(f"[harness] mock healthy at {ws_url}")

        # Import the ladder only now (post-env-export).
        from tests.ci import smoke_ladder as L
        results = L.run_ladder(stop_on_fail=True)
        L.write_junit(results, junit_path)
        print(f"\n[harness] junit written to {junit_path}")
        rc = L.summarize(results)
        return rc
    finally:
        print("[harness] stopping mock write_service")
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
