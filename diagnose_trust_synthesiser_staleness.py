#!/usr/bin/env python3
"""
Diagnostic utility: investigate why trust_synthesiser is stale at 1h26m (threshold 3600s).

Must:
1. Query write_service /query for trust_synthesiser last_heartbeat from service_health
2. Query mcp_server_registry for count of servers with last_assessed > 7 days old
3. Check for lockfile or busy flag
4. Do NOT rebuild trust_synthesiser (protected file per ALREADY_BUILT list)

Output: diagnostic JSON with findings:
  - daemon_status
  - stale_server_count
  - actionable_recommendation
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import requests

# Configuration
WRITE_SERVICE_HOST = os.environ.get("WRITE_SERVICE_HOST", "127.0.0.1")
WRITE_SERVICE_PORT = int(os.environ.get("WRITE_SERVICE_PORT", "8772"))
WRITE_SERVICE_URL = f"http://{WRITE_SERVICE_HOST}:{WRITE_SERVICE_PORT}"
HTTP_TIMEOUT = 10
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "outputs/goose"))
OUTPUT_FILE = OUTPUT_DIR / "diagnose_trust_synthesiser_staleness.json"
STALE_THRESHOLD_SECONDS = 3600  # 1 hour
LOCKFILE_PATH = Path("/tmp/trust_synthesiser.lock")
STALE_SERVER_DAYS = 7


def _make_request(method: str, endpoint: str, **kwargs) -> Optional[dict]:
    """Make HTTP request with timeout, return JSON or None on failure."""
    url = f"{WRITE_SERVICE_URL}{endpoint}"
    kwargs.setdefault("timeout", HTTP_TIMEOUT)
    try:
        response = requests.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None


def query_trust_synthesiser_heartbeat() -> Optional[dict]:
    """Query service_health for trust_synthesiser last_heartbeat."""
    payload = {
        "sql": (
            "SELECT service, status, last_heartbeat, meta "
            "FROM service_health "
            "WHERE service = 'trust_synthesiser' "
            "LIMIT 1"
        ),
        "params": []
    }
    result = _make_request("POST", "/query", json=payload)
    if result and isinstance(result, dict):
        rows = result.get("rows", [])
        if rows:
            return rows[0]
    return None


def query_stale_server_count() -> int:
    """Query mcp_server_registry for count of servers with last_assessed > 7 days old."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_SERVER_DAYS)
    sql = (
        "SELECT COUNT(*) AS stale_count "
        "FROM mcp_server_registry "
        "WHERE last_assessed < ? OR last_assessed IS NULL"
    )
    payload = {"sql": sql, "params": [cutoff.isoformat()]}
    result = _make_request("POST", "/query", json=payload)
    if result and isinstance(result, dict):
        rows = result.get("rows", [])
        if rows:
            return rows[0].get("stale_count", 0) or 0
    return 0


def check_lockfile() -> dict:
    """Check for trust_synthesiser lockfile or busy flag."""
    info: dict[str, Any] = {
        "lockfile_exists": LOCKFILE_PATH.exists(),
        "lockfile_path": str(LOCKFILE_PATH),
    }
    if LOCKFILE_PATH.exists():
        try:
            info["lockfile_content"] = LOCKFILE_PATH.read_text().strip()
        except Exception:
            info["lockfile_content"] = None
    # Check for running process
    try:
        result = subprocess.run(
            ["pgrep", "-f", "trust_synthesiser"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        info["process_pids"] = pids
        info["process_count"] = len(pids)
    except Exception:
        info["process_pids"] = []
        info["process_count"] = 0
    return info


def compute_age_seconds(ts: Optional[datetime]) -> Optional[int]:
    """Compute age in seconds from a timestamp to now."""
    if ts is None:
        return None
    delta = datetime.now(timezone.utc) - ts
    return int(delta.total_seconds())


def parse_heartbeat(record: Optional[dict]) -> Optional[datetime]:
    """Parse last_heartbeat from service_health record."""
    if not record:
        return None
    raw = record.get("last_heartbeat")
    if not raw:
        return None
    try:
        ts_str = str(raw).replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def _backup_output_file(filepath: Path) -> Optional[Path]:
    """Create timestamped backup of existing output file."""
    if filepath.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup = filepath.with_name(f"{filepath.stem}_backup_{ts}{filepath.suffix}")
        shutil.copy2(filepath, backup)
        return backup
    return None


def diagnose(
    _heartbeat_fn: Callable[[], Optional[dict]] = query_trust_synthesiser_heartbeat,
    _stale_fn: Callable[[], int] = query_stale_server_count,
    _lock_fn: Callable[[], dict] = check_lockfile,
) -> dict:
    """
    Run all diagnostic checks and return structured findings.

    Args:
        _heartbeat_fn: injectable override for query_trust_synthesiser_heartbeat
        _stale_fn:     injectable override for query_stale_server_count
        _lock_fn:      injectable override for check_lockfile

    Returns:
        dict with keys: daemon_status, stale_server_count, actionable_recommendation,
                        plus evidence detail keys.
    """
    heartbeat_record = _heartbeat_fn()
    heartbeat_ts = parse_heartbeat(heartbeat_record)
    age_seconds = compute_age_seconds(heartbeat_ts)

    stale_server_count = _stale_fn()
    lock_info = _lock_fn()

    # Determine daemon status
    if age_seconds is None:
        daemon_status = "no_heartbeat_recorded"
        status_detail = "trust_synthesiser has never sent a heartbeat to service_health"
    elif age_seconds > STALE_THRESHOLD_SECONDS:
        daemon_status = "stale"
        status_detail = (
            f"trust_synthesiser heartbeat is {age_seconds}s old "
            f"(threshold: {STALE_THRESHOLD_SECONDS}s)"
        )
    else:
        daemon_status = "healthy"
        status_detail = f"trust_synthesiser heartbeat is {age_seconds}s old"

    # Build actionable recommendation
    if daemon_status == "no_heartbeat_recorded":
        if lock_info["lockfile_exists"]:
            recommendation = (
                "trust_synthesiser has no heartbeat but lockfile exists at "
                f"{LOCKFILE_PATH}. The process may have crashed while holding the lock. "
                "Manually remove the lockfile and restart the daemon: "
                "rm /tmp/trust_synthesiser.lock && supervisorctl restart trust_synthesiser"
            )
        else:
            recommendation = (
                "trust_synthesiser has never sent a heartbeat. "
                "Check if the daemon is running: supervisorctl status trust_synthesiser. "
                "If not running, start it: supervisorctl start trust_synthesiser"
            )
    elif daemon_status == "stale":
        if lock_info["lockfile_exists"]:
            pid = lock_info.get("lockfile_content")
            proc_count = lock_info.get("process_count", 0)
            if proc_count == 0:
                recommendation = (
                    f"trust_synthesiser is stale ({age_seconds}s) and lockfile exists "
                    f"but no process is running. Remove stale lockfile and restart: "
                    "rm /tmp/trust_synthesiser.lock && supervisorctl restart trust_synthesiser"
                )
            else:
                recommendation = (
                    f"trust_synthesiser is stale ({age_seconds}s) but appears to be running "
                    f"(lockfile held by PID {pid}, {proc_count} process(es) found). "
                    "The process may be hung. Consider: "
                    "kill $(pgrep -f trust_synthesiser) && rm /tmp/trust_synthesiser.lock "
                    "&& supervisorctl restart trust_synthesiser"
                )
        else:
            recommendation = (
                f"trust_synthesiser is stale ({age_seconds}s) and no lockfile exists. "
                "The daemon may have crashed without leaving a lockfile. "
                "Restart: supervisorctl restart trust_synthesiser"
            )
    else:
        recommendation = "trust_synthesiser is healthy. No action required."

    findings: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "daemon_status": daemon_status,
        "daemon_age_seconds": age_seconds,
        "daemon_status_detail": status_detail,
        "stale_threshold_seconds": STALE_THRESHOLD_SECONDS,
        "stale_server_count": stale_server_count,
        "stale_server_days_threshold": STALE_SERVER_DAYS,
        "lockfile_info": lock_info,
        "heartbeat_record": heartbeat_record,
        "actionable_recommendation": recommendation,
    }

    return findings


def run() -> dict:
    """Execute diagnosis, write JSON output, return findings."""
    findings = diagnose()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _backup_output_file(OUTPUT_FILE)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(findings, fh, indent=2, ensure_ascii=False)

    return findings


def smoke_test() -> bool:
    """
    Self-smoke test: exercise diagnose() with injected mock functions.
    Returns True if all tests pass.
    """
    now = datetime.now(timezone.utc)
    stale_ts = now - timedelta(hours=2)
    healthy_ts = now - timedelta(minutes=30)

    test_cases = [
        {
            "name": "stale_daemon_with_lockfile",
            "heartbeat_record": {
                "service": "trust_synthesiser",
                "status": "running",
                "last_heartbeat": stale_ts.isoformat(),
            },
            "stale_count": 5,
            "lock_info": {
                "lockfile_exists": True,
                "lockfile_path": str(LOCKFILE_PATH),
                "lockfile_content": "12345",
                "process_pids": ["12345"],
                "process_count": 1,
            },
            "expected_status": "stale",
        },
        {
            "name": "healthy_daemon_no_lock",
            "heartbeat_record": {
                "service": "trust_synthesiser",
                "status": "running",
                "last_heartbeat": healthy_ts.isoformat(),
            },
            "stale_count": 0,
            "lock_info": {
                "lockfile_exists": False,
                "lockfile_path": str(LOCKFILE_PATH),
                "lockfile_content": None,
                "process_pids": ["67890"],
                "process_count": 1,
            },
            "expected_status": "healthy",
        },
        {
            "name": "no_heartbeat_with_stale_lock",
            "heartbeat_record": None,
            "stale_count": 10,
            "lock_info": {
                "lockfile_exists": True,
                "lockfile_path": str(LOCKFILE_PATH),
                "lockfile_content": "99999",
                "process_pids": [],
                "process_count": 0,
            },
            "expected_status": "no_heartbeat_recorded",
        },
    ]

    print("Running smoke tests for diagnose_trust_synthesiser_staleness.py")
    print("-" * 60)

    all_passed = True

    for tc in test_cases:
        result = diagnose(
            _heartbeat_fn=lambda r=tc["heartbeat_record"]: r,
            _stale_fn=lambda n=tc["stale_count"]: n,
            _lock_fn=lambda l=tc["lock_info"]: l,
        )
        actual = result["daemon_status"]
        passed = actual == tc["expected_status"]
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {tc['name']}")
        print(f"       Expected: {tc['expected_status']}  Got: {actual}")
        if not passed:
            all_passed = False
            print(f"       Recommendation: {result['actionable_recommendation'][:80]}...")

    print("-" * 60)
    print(f"Smoke test result: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    return all_passed


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--smoke-test":
        success = smoke_test()
        sys.exit(0 if success else 1)

    result = run()
    print(json.dumps(result, indent=2))
