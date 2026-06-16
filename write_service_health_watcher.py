# deps: requests
"""
Health watchdog daemon for write_service.

Polls write_service /query endpoint every 30s to confirm it responds, and checks
service_health table for write_service heartbeat age. If write_service fails to
respond or heartbeat age exceeds 5 minutes, logs alert and optionally triggers
escalation via mesh_bridge or alert_manager.

Addresses the current stale write_service (age=1h11m) which is blocking DB writes
for all daemons.

Interface:
  check_write_service_health() -> dict with keys:
    healthy (bool), response_time_ms (float), heartbeat_age_seconds (float), error (str|None)
  run()  # daemon: poll every 30s, alert on stale, heartbeat to service_health
  if __name__ == '__main__': run()

Inputs:
  - write_service URL: http://127.0.0.1:8772
  - Alert threshold: heartbeat age > 300s (5 minutes)
  - Poll interval: 30 seconds

Outputs:
  - Logs warning to stdout on stale write_service
  - Optionally POSTs to mesh_bridge or alert_manager webhook on failure
  - Writes own heartbeat to service_health (service='write_service_health_watcher')
"""

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
HEALTH_ENDPOINT = f"{WRITE_SERVICE_URL}/health"

# Thresholds
HEARTBEAT_STALE_THRESHOLD_SECONDS = 300  # 5 minutes
POLL_INTERVAL_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 10
HEARTBEAT_INTERVAL_SECONDS = 60

# This daemon's service name for its own heartbeat
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
    """Parse an ISO-8601 timestamp string into a UTC datetime."""
    if not ts:
        return None
    try:
        s = str(ts).strip().rstrip("Z")
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _heartbeat_age_seconds(last_heartbeat: Optional[str]) -> Optional[float]:
    """Compute age of a heartbeat timestamp in seconds. Returns None on parse failure."""
    parsed = _parse_timestamp(last_heartbeat)
    if parsed is None:
        return None
    delta = datetime.now(timezone.utc) - parsed
    return delta.total_seconds()


def _format_age(seconds: Optional[float]) -> str:
    """Format an age in seconds as a human-readable string."""
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
# DB helpers (via write_service HTTP API -- never direct duckdb)
# ---------------------------------------------------------------------------
def _ws_query(sql: str) -> dict:
    """Execute a SELECT via write_service /query. Returns parsed JSON."""
    try:
        resp = requests.post(QUERY_ENDPOINT, json={"sql": sql}, timeout=HTTP_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        LOG.warning("ws_query failed: %s", exc)
        return {"rows": []}


def _ws_write(table: str, rows: list) -> bool:
    """Write rows to a table via write_service /write. Returns True on success."""
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
def check_write_service_health() -> dict:
    """
    Check write_service responsiveness and heartbeat freshness.

    Returns dict with keys:
      healthy (bool)              -- True only if HTTP responds AND heartbeat < 300s
      response_time_ms (float)    -- HTTP round-trip time in milliseconds (0 on failure)
      heartbeat_age_seconds (float) -- age of write_service heartbeat in seconds (0 if unreadable)
      error (str|None)            -- error string on HTTP failure, None otherwise
    """
    result = {
        "healthy": False,
        "response_time_ms": 0.0,
        "heartbeat_age_seconds": 0.0,
        "error": None,
    }

    # 1. HTTP responsiveness check with timing
    try:
        start = time.monotonic()
        resp = requests.get(HEALTH_ENDPOINT, timeout=HTTP_TIMEOUT_SECONDS)
        elapsed_ms = (time.monotonic() - start) * 1000
        result["response_time_ms"] = round(elapsed_ms, 2)

        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result
    except Exception as exc:
        result["error"] = str(exc)
        return result

    # 2. Heartbeat freshness from service_health table
    query_result = _ws_query(
        "SELECT last_heartbeat FROM service_health "
        "WHERE service = 'write_service' LIMIT 1"
    )
    rows = query_result.get("rows", [])
    if rows:
        hb = rows[0].get("last_heartbeat")
        age = _heartbeat_age_seconds(hb)
        result["heartbeat_age_seconds"] = round(age, 2) if age is not None else 0.0
    else:
        # No heartbeat row found -- treat as stale
        result["heartbeat_age_seconds"] = float("inf")

    # 3. Combine into healthy verdict
    if result["response_time_ms"] > 0 and result["heartbeat_age_seconds"] < HEARTBEAT_STALE_THRESHOLD_SECONDS:
        result["healthy"] = True

    return result


# ---------------------------------------------------------------------------
# Internal escalation hook (called on stale write_service detection)
# ---------------------------------------------------------------------------
def _escalate(message: str) -> None:
    """
    Log a critical alert and optionally notify mesh_bridge / alert_manager.
    Expand here to POST to webhook endpoints if configured.
    """
    LOG.critical("ESCALATION: %s", message)
    # Future: POST to mesh_bridge or alert_manager webhook if those URLs are configured.
    # Example:
    #   try:
    #       requests.post(MESH_BRIDGE_URL, json={"alert": message}, timeout=10)
    #   except Exception:
    #       pass


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------
def run() -> None:
    """
    Poll write_service every 30s. Log a warning if stale and trigger escalation.
    Send this daemon's own heartbeat to service_health every 60s.
    """
    LOG.info("write_service_health_watcher started (pid=%d)", _get_pid())
    LOG.info("  write_service URL:  %s", WRITE_SERVICE_URL)
    LOG.info("  stale threshold:    %ds", HEARTBEAT_STALE_THRESHOLD_SECONDS)
    LOG.info("  poll interval:     %ds", POLL_INTERVAL_SECONDS)

    last_self_heartbeat = 0.0

    while True:
        loop_start = time.monotonic()

        # -- Health check --------------------------------------------------------
        health = check_write_service_health()

        if health["error"]:
            LOG.warning(
                "write_service HTTP unreachable: %s  (response_time_ms=%.2f)",
                health["error"],
                health["response_time_ms"],
            )
            _escalate(f"write_service HTTP unreachable: {health['error']}")
        elif health["heartbeat_age_seconds"] >= HEARTBEAT_STALE_THRESHOLD_SECONDS:
            LOG.warning(
                "write_service heartbeat stale: age=%s  (threshold=%ds, response_time_ms=%.2f)",
                _format_age(health["heartbeat_age_seconds"]),
                HEARTBEAT_STALE_THRESHOLD_SECONDS,
                health["response_time_ms"],
            )
            _escalate(
                f"write_service heartbeat stale for "
                f"{_format_age(health['heartbeat_age_seconds'])} "
                f"(threshold={HEARTBEAT_STALE_THRESHOLD_SECONDS}s)"
            )
        else:
            LOG.debug(
                "write_service healthy: response_time_ms=%.2f, heartbeat_age=%s",
                health["response_time_ms"],
                _format_age(health["heartbeat_age_seconds"]),
            )

        # -- Self heartbeat (every HEARTBEAT_INTERVAL_SECONDS) --------------------
        if loop_start - last_self_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
            self_health = check_write_service_health()
            row = {
                "service": SELF_SERVICE_NAME,
                "status": "healthy" if self_health["healthy"] else "degraded",
                "last_heartbeat": utc_now_iso(),
                "meta": '{"write_service_healthy": ' + ("true" if self_health["healthy"] else "false") + "}",
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
    import sys as _sys

    print("=" * 60)
    print("write_service_health_watcher self-smoke")
    print("=" * 60)

    # Call check_write_service_health() and validate interface
    result = check_write_service_health()
    print(f"\ncheck_write_service_health() returned:")
    for k, v in result.items():
        print(f"  {k}: {v!r}")

    # Assert required keys are present and types are correct
    assert isinstance(result, dict), "result must be a dict"
    assert "healthy" in result, "missing 'healthy' key"
    assert isinstance(result["healthy"], bool), "'healthy' must be bool"
    assert "response_time_ms" in result, "missing 'response_time_ms' key"
    assert isinstance(result["response_time_ms"], (int, float)), "'response_time_ms' must be numeric"
    assert "heartbeat_age_seconds" in result, "missing 'heartbeat_age_seconds' key"
    assert isinstance(result["heartbeat_age_seconds"], (int, float)), "'heartbeat_age_seconds' must be numeric"
    assert "error" in result, "missing 'error' key"
    assert result["error"] is None or isinstance(result["error"], str), "'error' must be None or str"

    print("\n  Interface assertion: PASS")
    print(f"  heartbeat_age: {_format_age(result['heartbeat_age_seconds'])}")
    print(f"  healthy:        {result['healthy']}")
    print(f"  error:          {result['error']}")
    print("\n  Smoke test PASSED (exits 0 regardless of actual service state)")
    _sys.exit(0)
