import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SERVICE_NAME = "audit_log_archival_gateway"
SERVICE_PORT = None
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
LOG_FILE = f"/home/workspace/logs/{SERVICE_NAME}.log"
POLL_SECS = 3600
ARCHIVE_THRESHOLD_DAYS = 90
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0

LOG_DIR = Path(LOG_FILE).parent
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger(__name__)


def ws_query(sql: str) -> list:
    payload = {"sql": sql}
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_write(table: str, rows: list) -> None:
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(WRITE_SERVICE_URL + "/write", json=payload, timeout=60)
    resp.raise_for_status()


def ws_execute(sql: str) -> None:
    payload = {"sql": sql}
    resp = requests.post(EXECUTE_URL, json=payload, timeout=60)
    resp.raise_for_status()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_single_instance() -> None:
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        old_pid = int(pid_path.read_text().strip())
        try:
            os.kill(old_pid, 0)
            log.error("Another instance already running with PID %d", old_pid)
            sys.exit(1)
        except OSError:
            log.warning("Stale PID file for PID %d, removing", old_pid)
            pid_path.unlink(missing_ok=True)
    pid_path.write_text(str(os.getpid()))


def remove_pid_file() -> None:
    Path(PID_FILE).unlink(missing_ok=True)


def signal_handler(signum: int, frame) -> None:
    signame = signal.Signals(signum).name
    log.info("Received %s, shutting down gracefully", signame)
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status: str = "running", meta: str = "") -> None:
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": utc_now_iso(),
            "status": status,
            "ts": utc_now_iso(),
            "meta": meta
        }])
    except Exception as e:
        log.warning("Failed to send heartbeat: %s", e)


def ensure_archive_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_audit_archive (
        id          BIGINT,
        target_server_id  VARCHAR,
        event_type  VARCHAR,
        actor       VARCHAR,
        detail      VARCHAR,
        created_at  TIMESTAMP,
        archived_at TIMESTAMP,
        PRIMARY KEY (id, archived_at)
    )
    """
    try:
        ws_execute(sql)
        log.info("Ensured mcp_audit_archive table exists")
    except Exception as e:
        log.error("Failed to create mcp_audit_archive table: %s", e)
        raise


def archive_old_entries() -> int:
    rows = ws_query(f"""
        SELECT id, target_server_id, event_type, actor, detail, created_at
        FROM audit_log
        WHERE created_at < NOW() - INTERVAL '{ARCHIVE_THRESHOLD_DAYS} days'
        ORDER BY created_at ASC
        LIMIT 5000
    """)
    if not rows:
        log.info("No audit_log entries older than %d days to archive", ARCHIVE_THRESHOLD_DAYS)
        return 0

    log.info("Found %d audit_log entries to archive", len(rows))
    archived_count = 0
    archived_ids = []

    for row in rows:
        archive_row = {
            "id": row["id"],
            "target_server_id": row.get("target_server_id"),
            "event_type": row["event_type"],
            "actor": row.get("actor"),
            "detail": row.get("detail"),
            "created_at": row["created_at"],
            "archived_at": utc_now_iso(),
        }
        _write_with_retry("mcp_audit_archive", [archive_row])
        archived_ids.append(row["id"])
        archived_count += 1

    if archived_ids:
        id_list = ",".join(str(i) for i in archived_ids)
        delete_sql = f"DELETE FROM audit_log WHERE id IN ({id_list})"
        _write_with_retry_delete(delete_sql)
        log.info("Archived and deleted %d audit_log entries", archived_count)

    return archived_count


def _write_with_retry(table: str, rows: list) -> None:
    delay = RETRY_BASE_DELAY
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            ws_write(table, rows)
            return
        except requests.HTTPError as e:
            if e.response is not None and 500 <= e.response.status_code < 600:
                log.warning("write_service returned %d for %s (attempt %d/%d), retrying in %.1fs",
                            e.response.status_code, table, attempt, MAX_RETRIES, delay)
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(delay)
                delay *= 2
            else:
                raise
        except Exception as e:
            log.warning("write_service error for %s (attempt %d/%d): %s", table, attempt, MAX_RETRIES, e)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(delay)
            delay *= 2


def _write_with_retry_delete(sql: str) -> None:
    delay = RETRY_BASE_DELAY
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            ws_execute(sql)
            return
        except requests.HTTPError as e:
            if e.response is not None and 500 <= e.response.status_code < 600:
                log.warning("write_service returned %d for DELETE (attempt %d/%d), retrying in %.1fs",
                            e.response.status_code, attempt, MAX_RETRIES, delay)
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(delay)
                delay *= 2
            else:
                raise
        except Exception as e:
            log.warning("write_service error for DELETE (attempt %d/%d): %s", attempt, MAX_RETRIES, e)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(delay)
            delay *= 2


def cycle() -> None:
    log.info("Starting archival cycle for audit_log entries older than %d days", ARCHIVE_THRESHOLD_DAYS)
    total_archived = 0
    batch = 0
    while True:
        count = archive_old_entries()
        total_archived += count
        batch += 1
        if count < 5000:
            break
        log.info("Archive batch %d complete (%d entries); fetching next batch", batch, count)
    log.info("Archival cycle complete. Total entries archived this cycle: %d across %d batches",
             total_archived, batch)
    send_heartbeat(status="running", meta=f"archived={total_archived}")


def run() -> None:
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info("Starting %s", SERVICE_NAME)
    ensure_archive_table()
    while True:
        try:
            cycle()
        except Exception as e:
            log.error("Archival cycle failed: %s", e)
            send_heartbeat(status="error", meta=str(e))
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()