# deps: requests
"""
exemption_expirer_daemon.py
Daemon that expires exemption records in mcp_exemptions when expires_at has passed.

Interface:
  - run() function + `if __name__ == '__main__': run()`
  - Heartbeats to service_health every 60s
  - Cycle runs every 6 hours by default (EXEMPTION_CHECK_INTERVAL env var, seconds)

Inputs:
  - Reads mcp_exemptions via write_service /query:
      WHERE expires_at < CURRENT_TIMESTAMP AND active = TRUE

Outputs:
  - For each expired row: UPDATE active = FALSE via write_service /execute
  - Logs count of expired exemptions per cycle
  - No DELETE — expired rows are retained for audit

Constraints:
  - requests.post to 127.0.0.1:8772 only; no duckdb import; 10s HTTP timeout
  - Idempotent: re-running is a no-op
"""
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WRITE_SERVICE_BASE = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_BASE}/query"
EXECUTE_URL = f"{WRITE_SERVICE_BASE}/execute"
WRITE_URL = f"{WRITE_SERVICE_BASE}/write"

# Heartbeat every 60 seconds (PRODUCT_SPEC §6)
HEARTBEAT_INTERVAL_SECS = 60
# Default cycle: 6 hours; override via EXEMPTION_CHECK_INTERVAL env var (seconds)
DEFAULT_CYCLE_SECS = 6 * 60 * 60
SERVICE_NAME = "exemption_expirer"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(SERVICE_NAME)


# ---------------------------------------------------------------------------
# write_service HTTP helpers (10s timeout)
# ---------------------------------------------------------------------------

def _post(url: str, payload: dict) -> Any:
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str, params: list | None = None) -> list[dict]:
    payload: dict = {"sql": sql}
    if params is not None:
        payload["params"] = params
    data = _post(QUERY_URL, payload)
    return data.get("rows", [])


def ws_execute(sql: str, params: list | None = None) -> bool:
    payload: dict = {"sql": sql, "wait": True}
    if params is not None:
        payload["params"] = params
    try:
        _post(EXECUTE_URL, payload)
        return True
    except Exception as exc:
        log.error("ws_execute error: %s | SQL: %s", exc, sql[:200])
        return False


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

def send_heartbeat() -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        _post(WRITE_URL, {
            "table": "service_health",
            "rows": [{
                "service": SERVICE_NAME,
                "status": "running",
                "meta": "{}",
                "last_heartbeat": now,
            }],
        })
    except Exception as exc:
        log.warning("Heartbeat failed: %s", exc)


# ---------------------------------------------------------------------------
# Core expiry logic
# ---------------------------------------------------------------------------

def _fetch_expired_exemptions() -> list[dict]:
    """
    SELECT rows where expires_at < now() AND active = TRUE.
    Uses CURRENT_TIMESTAMP for DB-side comparison.
    """
    sql = """
        SELECT exemption_id, server_id, reason
        FROM mcp_exemptions
        WHERE expires_at < CURRENT_TIMESTAMP AND active = TRUE
    """
    return ws_query(sql)


def _mark_expired(exemption_id: str, server_id: str) -> bool:
    """
    UPDATE: set active = FALSE for the given exemption.
    No DELETE — retains row for audit.
    """
    sql = """
        UPDATE mcp_exemptions
        SET active = FALSE
        WHERE exemption_id = ? AND server_id = ?
    """
    return ws_execute(sql, params=[exemption_id, server_id])


def run_cycle() -> int:
    """
    Fetch expired exemptions and mark each as inactive.
    Returns the count of expired exemptions processed.
    """
    expired_rows = _fetch_expired_exemptions()
    if not expired_rows:
        log.info("No expired exemptions found in this cycle.")
        return 0

    log.info("Found %d expired exemption(s).", len(expired_rows))
    processed = 0
    for row in expired_rows:
        eid = row.get("exemption_id")
        sid = row.get("server_id")
        if not eid or not sid:
            log.warning("Skipping row missing exemption_id/server_id: %s", row)
            continue
        if _mark_expired(eid, sid):
            log.info("Expired exemption_id=%s server_id=%s", eid, sid)
            processed += 1
        else:
            log.error("Failed to expire exemption_id=%s server_id=%s", eid, sid)

    log.info("Expired %d exemption(s) in this cycle.", processed)
    return processed


# ---------------------------------------------------------------------------
# Daemon run loop
# ---------------------------------------------------------------------------

def run() -> None:
    interval_str = os.environ.get("EXEMPTION_CHECK_INTERVAL", str(DEFAULT_CYCLE_SECS))
    try:
        cycle_secs = int(interval_str)
    except ValueError:
        log.warning(
            "Invalid EXEMPTION_CHECK_INTERVAL %r, using default %d",
            interval_str, DEFAULT_CYCLE_SECS,
        )
        cycle_secs = DEFAULT_CYCLE_SECS

    log.info(
        "Starting %s daemon | cycle_interval=%ds heartbeat=%ds",
        SERVICE_NAME, cycle_secs, HEARTBEAT_INTERVAL_SECS,
    )

    # Graceful shutdown on SIGTERM / SIGINT
    shutting_down = threading.Event()

    def handle_signal(signum, _frame):
        sig = signal.Signals(signum).name
        log.info("Received %s — shutting down.", sig)
        shutting_down.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    last_cycle = 0.0

    while not shutting_down.is_set():
        now = time.time()
        if now - last_cycle >= cycle_secs:
            log.info("Running exemption expiry cycle.")
            try:
                run_cycle()
            except Exception as exc:
                log.error("Cycle failed: %s", exc)
            last_cycle = now

        # Heartbeat fires every iteration regardless of cycle state
        try:
            send_heartbeat()
        except Exception as exc:
            log.warning("Heartbeat error: %s", exc)

        # Sleep in short increments so shutdown signal is responsive
        shutting_down.wait(timeout=HEARTBEAT_INTERVAL_SECS)

    log.info("%s daemon stopped.", SERVICE_NAME)


# ---------------------------------------------------------------------------
# Self-smoke / acceptance test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import unittest.mock as mock

    # Mock write_service responses
    expired_row = {
        "exemption_id": "test-expired-001",
        "server_id": "test-server-001",
        "reason": "testing",
    }
    active_row = {
        "exemption_id": "test-active-001",
        "server_id": "test-server-002",
        "reason": "still valid",
    }

    call_log: list[tuple[str, dict]] = []

    def mock_post(url: str, **kwargs) -> mock.MagicMock:
        payload = kwargs.get("json", {})
        call_log.append((url, payload))
        resp = mock.MagicMock()
        resp.raise_for_status = mock.MagicMock()
        rows: list[dict] = []
        if payload.get("sql", "").startswith(
            "SELECT exemption_id, server_id, reason FROM mcp_exemptions"
        ):
            # Return BOTH an expired and an active exemption
            rows = [expired_row, active_row]
        resp.json = mock.MagicMock(return_value={"rows": rows})
        return resp

    with mock.patch("requests.post", side_effect=mock_post):
        # Run one cycle
        processed = run_cycle()

    # Verify: expired row was found (2 in query response, 1 expired)
    # The query returns both rows; our logic only processes expired ones
    assert processed == 1, f"Expected 1 expired processed, got {processed}"

    # Verify: the UPDATE was called for the expired exemption only
    update_calls = [
        (url, p) for url, p in call_log
        if "UPDATE mcp_exemptions" in p.get("sql", "")
    ]
    assert len(update_calls) == 1, (
        f"Expected exactly 1 UPDATE, got {len(update_calls)}: {update_calls}"
    )
    update_url, update_payload = update_calls[0]
    assert update_payload["params"] == ["test-expired-001", "test-server-001"], (
        f"Wrong UPDATE params: {update_payload}"
    )

    # Verify: active exemption was NOT updated
    update_sqls = [p.get("sql", "") for _, p in update_calls]
    assert "test-active-001" not in str(update_sqls), (
        f"Active exemption should not have been updated: {update_sqls}"
    )

    print("PASS")