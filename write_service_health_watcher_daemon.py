# deps: requests
"""
Health watchdog daemon for write_service.

write_service is the single DB access point for all Sentinel daemons. When it
goes stale (>300s without heartbeat), the entire pipeline stalls silently. This
watcher detects staleness and fires an escalation signal.

Interface:
  check_health() -> dict:  queries service_health for write_service row,
                           returns {status, last_heartbeat, age_seconds}
  escalate() -> None:      POSTs alert or writes mcp_threat_associations row
  run() -> None:           main daemon loop, 30s cycle, heartbeat every 60s

Inputs:
  - write_service: http://127.0.0.1:8772
  - query: SELECT last_heartbeat FROM service_health WHERE service='write_service'
  - staleness threshold: 300s

Outputs:
  - Prints status each cycle: "write_service: ok (age=Ns)" or
    "write_service: STALE (age=Ns)"
  - On staleness: fires escalation, logs to audit_trail via write_service
  - Heartbeat to service_health every 60s

Constraints:
  - stdlib + requests only
  - NO direct duckdb import
  - All DB via write_service POST /query and /write
  - 10s timeout on HTTP calls
  - Must not spam escalation — once per staleness event until recovered
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_ENDPOINT = f"{WRITE_SERVICE_URL}/query"
WRITE_ENDPOINT = f"{WRITE_SERVICE_URL}/write"

STALENESS_THRESHOLD_SECONDS = 300  # 5 minutes
POLL_INTERVAL_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 10
SELF_HEARTBEAT_INTERVAL_SECONDS = 60

SELF_SERVICE_NAME = "write_service_health_watcher"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
LOG = logging.getLogger("write_service_health_watcher")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        s = str(ts).strip().rstrip("Z")
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _compute_age_seconds(ts: Optional[str]) -> Optional[float]:
    parsed = _parse_timestamp(ts)
    if parsed is None:
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _format_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "N/A"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# DB helpers (via write_service HTTP API — never direct duckdb)
# ---------------------------------------------------------------------------
def _ws_query(sql: str) -> dict:
    try:
        resp = requests.post(QUERY_ENDPOINT, json={"sql": sql}, timeout=HTTP_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        LOG.warning("ws_query failed: %s", exc)
        return {"rows": []}


def _ws_write(table: str, rows: list) -> bool:
    try:
        resp = requests.post(
            WRITE_ENDPOINT,
            json={"table": table, "rows": rows, "wait": True},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        LOG.warning("ws_write failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Public Interface
# ---------------------------------------------------------------------------
def check_health() -> dict:
    """
    Query service_health for write_service row.

    Returns dict with keys:
      status (str)               -- 'ok' or 'stale'
      last_heartbeat (str|None) -- ISO timestamp of last write_service heartbeat
      age_seconds (float)        -- age of last heartbeat in seconds
    """
    result = {
        "status": "unknown",
        "last_heartbeat": None,
        "age_seconds": 0.0,
    }

    query_result = _ws_query(
        "SELECT last_heartbeat FROM service_health "
        "WHERE service = 'write_service' LIMIT 1"
    )
    rows = query_result.get("rows", [])
    if rows:
        hb = rows[0].get("last_heartbeat")
        result["last_heartbeat"] = hb
        age = _compute_age_seconds(hb)
        result["age_seconds"] = round(age, 2) if age is not None else 0.0
    else:
        # No row found — treat as infinitely stale
        result["age_seconds"] = float("inf")

    if result["age_seconds"] < STALENESS_THRESHOLD_SECONDS:
        result["status"] = "ok"
    else:
        result["status"] = "stale"

    return result


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------
def escalate() -> None:
    """
    Fire an escalation for write_service staleness.

    Attempts to write a staleness record to mcp_threat_associations via
    write_service. Logs to audit_trail. Silently ignores failures.
    """
    now_iso = utc_now_iso()
    try:
        _ws_write("mcp_threat_associations", [{
            "server_id": "write_service",
            "threat_type": "staleness",
            "description": (
                f"write_service heartbeat exceeded "
                f"{STALENESS_THRESHOLD_SECONDS}s staleness threshold"
            ),
            "severity": "high",
            "detected_at": now_iso,
        }])
    except Exception:
        pass

    try:
        _ws_write("audit_log", [{
            "event_type": "escalation",
            "actor": SELF_SERVICE_NAME,
            "target_server_id": "write_service",
            "action": "staleness_detected",
            "outcome": "escalated",
            "details_json": '{"reason": "write_service heartbeat stale"}',
            "immutable": False,
            "timestamp": now_iso,
        }])
    except Exception:
        pass

    LOG.critical("ESCALATION: write_service heartbeat stale for >%ds", STALENESS_THRESHOLD_SECONDS)


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------
def run() -> None:
    """
    Main daemon loop.

    Polls write_service every POLL_INTERVAL_SECONDS (30s). Logs status each
    cycle. Fires escalation once per staleness event (not on every cycle).
    Sends this daemon's own heartbeat to service_health every 60s.
    """
    LOG.info("write_service_health_watcher started (pid=%d)", _get_pid())
    LOG.info("  write_service URL: %s", WRITE_SERVICE_URL)
    LOG.info("  staleness threshold: %ds", STALENESS_THRESHOLD_SECONDS)
    LOG.info("  poll interval: %ds", POLL_INTERVAL_SECONDS)

    was_stale = False  # Anti-spam: escalate once per staleness event
    last_self_heartbeat = 0.0

    while True:
        loop_start = time.monotonic()

        # -- Health check -------------------------------------------------------
        health = check_health()
        age_s = health["age_seconds"]
        status = health["status"]

        if status == "stale":
            LOG.warning("write_service: STALE (age=%s)", _format_age(age_s))
            if not was_stale:
                escalate()
                was_stale = True
        else:
            LOG.info("write_service: ok (age=%s)", _format_age(age_s))
            was_stale = False

        # -- Self heartbeat every SELF_HEARTBEAT_INTERVAL_SECONDS (60s) ----------
        if loop_start - last_self_heartbeat >= SELF_HEARTBEAT_INTERVAL_SECONDS:
            row = {
                "service": SELF_SERVICE_NAME,
                "status": status,
                "last_heartbeat": utc_now_iso(),
                "meta": '{"write_service_status": "' + status + '"}',
            }
            if _ws_write("service_health", [row]):
                LOG.debug("Self-heartbeat sent for %s", SELF_SERVICE_NAME)
            last_self_heartbeat = loop_start

        # -- Sleep to maintain poll interval ------------------------------------
        elapsed = time.monotonic() - loop_start
        sleep_time = max(0, POLL_INTERVAL_SECONDS - elapsed)
        time.sleep(sleep_time)


def _get_pid() -> int:
    try:
        import os
        return os.getpid()
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Self-smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("write_service_health_watcher self-smoke")
    print("=" * 60)

    result = check_health()
    print("\ncheck_health() returned:")
    for k, v in result.items():
        print(f"  {k}: {v!r}")

    # Acceptance: dict with 'status' key
    assert isinstance(result, dict), "result must be a dict"
    assert "status" in result, "missing 'status' key"
    assert isinstance(result["status"], str), "'status' must be str"
    assert result["status"] in ("ok", "stale", "unknown"), (
        f"'status' must be 'ok'|'stale'|'unknown', got {result['status']!r}"
    )
    assert "last_heartbeat" in result, "missing 'last_heartbeat' key"
    assert "age_seconds" in result, "missing 'age_seconds' key"
    assert isinstance(result["age_seconds"], (int, float)), "'age_seconds' must be numeric"

    print("\n  Interface assertion: PASS")
    print(f"  status:        {result['status']}")
    print(f"  age_seconds:   {result['age_seconds']}")
    print(f"  last_heartbeat: {result['last_heartbeat']}")

    if args.dry_run:
        print("\n  --dry-run: PASS")
    else:
        print("\n  Smoke test PASSED (exits 0 regardless of actual service state)")

    sys.exit(0)
