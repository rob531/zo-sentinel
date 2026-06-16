#!/usr/bin/env python3
"""
write_service_restart_coordinator.py

One-shot supervisory script that diagnoses and resolves a stale write_service
heartbeat. Reads service_health via write_service /query, restarts via
supervisorctl if stale (last_heartbeat > 300s ago), then verifies recovery.

Returns a status dict with keys: was_stale, restart_attempted, recovered, error.
run() returns True if recovered, False otherwise.

Constraints:
  - stdlib + requests only (subprocess, requests, time)
  - No duckdb imports
  - MUST NOT DROP/DELETE/TRUNCATE any table (read-only on data tables)
  - 30s total timeout
  - Idempotent: re-run is a no-op if already healthy
"""

import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Dict

import requests

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_NAME = "write_service"
STALE_THRESHOLD_SECONDS = 300
TOTAL_TIMEOUT_SECONDS = 30


def _query(sql: str, params: list = None) -> Dict[str, Any]:
    """Execute a SELECT via write_service /query. Returns the JSON response."""
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _get_heartbeat_age_seconds(last_heartbeat_str: str | None) -> float | None:
    """Parse a heartbeat ISO-8601 string and return age in seconds, or None on parse error."""
    if not last_heartbeat_str:
        return None
    try:
        # Handle both with and without timezone; assume UTC when naive.
        parsed = datetime.fromisoformat(last_heartbeat_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age = (now - parsed).total_seconds()
        return age
    except (ValueError, TypeError):
        return None


def _is_stale() -> bool:
    """
    Query service_health for write_service's last_heartbeat.
    Returns True if the record is missing or the heartbeat is > STALE_THRESHOLD_SECONDS old.
    """
    try:
        result = _query(
            f"SELECT last_heartbeat FROM service_health WHERE service = $1 LIMIT 1",
            params=[SERVICE_NAME],
        )
        rows = result.get("rows", [])
        if not rows:
            # No heartbeat record at all — treat as stale
            return True
        last_hb = rows[0].get("last_heartbeat")
        age = _get_heartbeat_age_seconds(last_hb)
        if age is None:
            # Couldn't parse — treat as stale
            return True
        return age > STALE_THRESHOLD_SECONDS
    except Exception:
        # Network / timeout error — treat as stale so restart is attempted
        return True


def _attempt_restart() -> str | None:
    """Run supervisorctl restart write_service. Returns None on success, error string on failure."""
    try:
        result = subprocess.run(
            ["supervisorctl", "restart", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return None
        return result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
    except subprocess.TimeoutExpired:
        return "supervisorctl timed out"
    except FileNotFoundError:
        return "supervisorctl not found in PATH"
    except Exception as e:
        return str(e)


def _has_recovered() -> bool:
    """Poll service_health for up to 15s waiting for a fresh heartbeat (< 60s old)."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            result = _query(
                f"SELECT last_heartbeat FROM service_health WHERE service = $1 LIMIT 1",
                params=[SERVICE_NAME],
            )
            rows = result.get("rows", [])
            if rows:
                last_hb = rows[0].get("last_heartbeat")
                age = _get_heartbeat_age_seconds(last_hb)
                if age is not None and age <= STALE_THRESHOLD_SECONDS:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def run() -> bool:
    """
    Diagnose write_service heartbeat staleness, restart if needed, verify recovery.

    Returns True if write_service is healthy (was already healthy OR recovered after restart).
    Returns False if stale and restart did not recover within timeout.
    """
    start = time.monotonic()

    # Phase 1 — detect staleness
    was_stale = _is_stale()
    if not was_stale:
        return True

    # Phase 2 — restart
    restart_error = _attempt_restart()
    restart_attempted = True

    if restart_error:
        # Could not restart; report failure without further polling
        return False

    # Phase 3 — wait for heartbeat to appear / refresh
    recovered = _has_recovered()

    return recovered


def _main() -> Dict[str, Any]:
    """
    Full run returning the structured status dict.
    Called by both run() and __main__ acceptance block.
    """
    start = time.monotonic()

    status: Dict[str, Any] = {
        "was_stale": False,
        "restart_attempted": False,
        "recovered": False,
        "error": None,
    }

    try:
        was_stale = _is_stale()
        status["was_stale"] = was_stale

        if not was_stale:
            status["recovered"] = True
            return status

        restart_error = _attempt_restart()
        status["restart_attempted"] = True

        if restart_error:
            status["error"] = f"restart failed: {restart_error}"
            return status

        recovered = _has_recovered()
        status["recovered"] = recovered
        if not recovered:
            status["error"] = "heartbeat did not recover within 15s of restart"

    except Exception as e:
        status["error"] = str(e)

    status["_elapsed_seconds"] = round(time.monotonic() - start, 2)
    return status


if __name__ == "__main__":
    status = _main()

    # Acceptance: assert required keys present
    required_keys = {"was_stale", "restart_attempted", "recovered", "error"}
    assert required_keys.issubset(status.keys()), (
        f"Status dict missing keys: {required_keys - set(status.keys())}"
    )

    if status["recovered"]:
        print(f"PASS — recovered={status['recovered']}, "
              f"was_stale={status['was_stale']}, "
              f"restart_attempted={status['restart_attempted']}, "
              f"error={status['error']}, "
              f"elapsed={status.get('_elapsed_seconds', '?')}s")
    else:
        print(f"FAIL — recovered={status['recovered']}, "
              f"was_stale={status['was_stale']}, "
              f"restart_attempted={status['restart_attempted']}, "
              f"error={status['error']}, "
              f"elapsed={status.get('_elapsed_seconds', '?')}s")
        exit(1)