# deps: requests
"""
exemption_expirer.py
Retention daemon that expires MCP exemptions past their valid_until date.
Nightly scan of mcp_exemptions; expire any record where expires_at < now().
Write audit_log entry per expiry. No other DB writes.
"""
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

import requests

SERVICE_NAME = "exemption_expirer"
SERVICE_PORT = None  # Not a FastAPI service
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://localhost:8772"
EXECUTE_URL = "http://localhost:8772/execute"
QUERY_URL = "http://localhost:8772/query"
WRITE_URL = "http://localhost:8772/write"
POLL_SECS = 60
SCAN_INTERVAL_SECS = 86400  # 24 hours

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(f"/home/workspace/logs/{SERVICE_NAME}.log")],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_single_instance():
    pid_str = str(os.getpid())
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = f.read().strip()
        if old_pid and old_pid != pid_str:
            try:
                os.kill(int(old_pid), 0)
                log.error("Another instance is running with PID %s. Exiting.", old_pid)
                sys.exit(1)
            except OSError:
                log.warning("Stale PID file; overwriting with %s", pid_str)
    with open(PID_FILE, "w") as f:
        f.write(pid_str)


def remove_pid_file():
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame):
    sig_name = signal.Signals(signum).name
    log.info("Received %s, shutting down gracefully.", sig_name)
    remove_pid_file()
    sys.exit(0)


def ws_query(sql, params=None):
    try:
        payload = {"sql": sql}
        if params is not None:
            payload["params"] = params
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error("ws_query failed: %s | SQL: %s", e, sql[:200])
        return []


def ws_execute(sql, params=None):
    try:
        payload = {"sql": sql, "wait": True}
        if params is not None:
            payload["params"] = params
        resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_execute failed: %s | SQL: %s", e, sql[:200])
        return False


def ws_write(table, rows):
    try:
        resp = requests.post(WRITE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_write failed: %s | table=%s", e, table)
        return False


def send_heartbeat():
    now = datetime.now(timezone.utc).isoformat()
    ws_write("service_health", [{"service": SERVICE_NAME, "last_heartbeat": now, "status": "running", "meta": "{}"}])


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def table_exists():
    result = ws_query(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'mcp_exemptions' LIMIT 1"
    )
    return len(result) > 0


def get_expired_exemptions():
    """Return rows where active=true and expires_at < now."""
    now = datetime.now(timezone.utc).isoformat()
    sql = (
        "SELECT exemption_id, server_id, reason "
        "FROM mcp_exemptions "
        "WHERE active = TRUE AND expires_at < %s"
    )
    return ws_query(sql, params=[now])


def mark_expired(exemption_id, server_id):
    """Set active=FALSE on the exemption record (no DELETE)."""
    now = datetime.now(timezone.utc).isoformat()
    sql = (
        "UPDATE mcp_exemptions SET active = FALSE, expires_at = %s "
        "WHERE exemption_id = %s AND server_id = %s"
    )
    return ws_execute(sql, params=[now, exemption_id, server_id])


def write_audit_log(server_id, exemption_id, reason):
    """Write an audit_log entry for an expired exemption."""
    now = datetime.now(timezone.utc).isoformat()
    ws_write("audit_log", [{
        "event_type": "exemption_expired",
        "action": "exemption_expired",
        "actor": SERVICE_NAME,
        "target_server_id": server_id,
        "outcome": "success",
        "details_json": f'{{"exemption_id": "{exemption_id}", "reason": "{reason}"}}',
        "immutable": True,
        "timestamp": now,
    }])


def cycle():
    """Scan for expired exemptions, mark inactive, write audit entries."""
    if not table_exists():
        log.info("mcp_exemptions table does not exist yet; skipping cycle.")
        return

    expired = get_expired_exemptions()
    if not expired:
        log.info("No expired exemptions found.")
        return

    log.info("Found %d expired exemption(s).", len(expired))
    expired_count = 0
    for row in expired:
        exemption_id = row.get("exemption_id")
        server_id = row.get("server_id")
        reason = row.get("reason") or ""
        if not exemption_id or not server_id:
            continue
        if mark_expired(exemption_id, server_id):
            write_audit_log(server_id, exemption_id, reason)
            expired_count += 1
            log.info(
                "Marked exemption expired: exemption_id=%s server_id=%s",
                exemption_id,
                server_id,
            )

    log.info("Expired %d exemption(s) in this cycle.", expired_count)


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info("Starting %s daemon (PID=%s)", SERVICE_NAME, os.getpid())
    last_full_scan = 0

    while True:
        now_ts = time.time()
        if now_ts - last_full_scan >= SCAN_INTERVAL_SECS:
            log.info("Running full exemption expiry scan.")
            cycle()
            last_full_scan = now_ts

        send_heartbeat()
        time.sleep(POLL_SECS)


# ---------------------------------------------------------------------------
# Self-smoke: test the module's core logic with a synthetic exemption row.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    past = "2020-01-01T00:00:00+00:00"
    synthetic_audit = []

    def mock_ws_query(sql, params=None):
        if "information_schema" in sql:
            return [{}]  # table exists
        if "active = TRUE" in sql:
            return [
                {
                    "exemption_id": "test-expired-001",
                    "server_id": "test-server-001",
                    "reason": "testing exemption expiry",
                }
            ]
        return []

    def mock_ws_execute(sql, params=None):
        return True

    def mock_ws_write(table, rows):
        if table == "audit_log":
            synthetic_audit.extend(rows)
        return True

    # Rewrite the module's ws_* functions in-place so the module's own
    # table_exists / get_expired_exemptions / mark_expired / write_audit_log
    # hit our mocks instead of making real HTTP calls.
    import sys as _sys
    mod = _sys.modules["__main__"]
    _orig_query = mod.ws_query
    _orig_exec = mod.ws_execute
    _orig_write = mod.ws_write

    try:
        mod.ws_query = mock_ws_query
        mod.ws_execute = mock_ws_execute
        mod.ws_write = mock_ws_write

        # Run cycle logic inline (no daemon fork, no sleep)
        if not table_exists():
            raise AssertionError("table_exists() returned False unexpectedly")

        expired_rows = get_expired_exemptions()
        if not expired_rows:
            raise AssertionError("get_expired_exemptions() returned empty unexpectedly")

        exemption_id = expired_rows[0]["exemption_id"]
        server_id = expired_rows[0]["server_id"]
        reason = expired_rows[0]["reason"]

        if not mark_expired(exemption_id, server_id):
            raise AssertionError("mark_expired() returned False")

        write_audit_log(server_id, exemption_id, reason)

        if not synthetic_audit:
            raise AssertionError("audit_log row was not written")

        audit_row = synthetic_audit[0]
        assert audit_row.get("action") == "exemption_expired", f"Wrong action: {audit_row}"
        assert audit_row.get("target_server_id") == "test-server-001", f"Wrong target_server_id: {audit_row}"
        assert "timestamp" in audit_row, f"Missing timestamp: {audit_row}"

        print("PASS: exemption_expirer self-smoke")
        sys.exit(0)
    except Exception as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
    finally:
        # Restore originals
        mod.ws_query = _orig_query
        mod.ws_execute = _orig_exec
        mod.ws_write = _orig_write