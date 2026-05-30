#!/usr/bin/env python3
"""
run_ci_frontend.py -- entrypoint for the hermetic front-end runner.

Mirrors run_ci_smoke.py but drives tests/ci/frontend_runner.py:

    1. start tests/mock_write_service.py on an ephemeral port
    2. wait for /health, export ZO_WRITE_SERVICE
    3. run the front-end ladder (html -> html_forms -> app_build -> routes)
    4. write junit (consumed by gh_actions_fetcher.py), summarize, exit

FE0/FE1 (HTML validation) run anywhere. FE2/FE3 (boot ui_server + drive its
routes) need ui_server's host path; in CI the workflow stages
/home/workspace/zo_sentinel first. Off-host they SKIP rather than fail.

Run from the repo root:

    python -m tests.ci.run_ci_frontend

Env knobs:
    CI_FE_WS_PORT    mock write_service port    (default 8772 -- ui_server's
                                                hardcoded write_service port, so
                                                the boot/auth/submission data
                                                path actually reaches the mock)
    CI_FE_JUNIT      junit output path          (default artifacts/ci_frontend_junit.xml)
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _wait_health(url: str, timeout_s: float = 30.0) -> bool:
    import requests
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if requests.get(url + "/health", timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def main() -> int:
    port = int(os.environ.get("CI_FE_WS_PORT", "8772"))
    ws_url = f"http://127.0.0.1:{port}"
    junit_path = Path(os.environ.get(
        "CI_FE_JUNIT", str(REPO_ROOT / "artifacts" / "ci_frontend_junit.xml")))

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
            print("WARN: mock write_service not healthy in 30s; "
                  "FE2/FE3 may degrade", file=sys.stderr)
        else:
            print(f"[harness] mock healthy at {ws_url}")

        from tests.ci import frontend_runner as FE
        from tests.ci.smoke_ladder import write_junit, summarize
        results = FE.run_frontend_ladder(stop_on_fail=True)
        write_junit(results, junit_path)
        print(f"\n[harness] junit written to {junit_path}")
        return summarize(results)
    finally:
        print("[harness] stopping mock write_service")
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
