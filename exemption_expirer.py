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


def ws_query(sql):
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error("ws_query failed: %s | SQL: %s", e, sql[:200])
        return []


def ws_execute(sql):
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
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


def table_exists():
    result = ws_query(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'mcp_exemptions' LIMIT 1"
    )
    return len(result) > 0


def get_expired_exemptions():
    now = datetime.now(timezone.utc).isoformat()
    sql = (
        "SELECT server_id, exemption_id "
        "FROM mcp_exemptions "
        "WHERE status = 'active' AND valid_until < %s"
    )
    return ws_query(sql)


def mark_expired(server_id, exemption_id):
    now = datetime.now(timezone.utc).isoformat()
    sql = (
        f"UPDATE mcp_exemptions SET status = 'expired', updated_at = '{now}' "
        f"WHERE server_id = '{server_id}' AND exemption_id = '{exemption_id}'"
    )
    return ws_execute(sql)


def delete_expired(server_id, exemption_id):
    sql = (
        f"DELETE FROM mcp_exemptions "
        f"WHERE server_id = '{server_id}' AND exemption_id = '{exemption_id}'"
    )
    return ws_execute(sql)


def cycle():
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
        server_id = row.get("server_id")
        exemption_id = row.get("exemption_id")
        if not server_id or not exemption_id:
            continue
        if mark_expired(server_id, exemption_id):
            expired_count += 1
            log.info("Marked exemption expired: server_id=%s exemption_id=%s", server_id, exemption_id)

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


if __name__ == "__main__":
    run()