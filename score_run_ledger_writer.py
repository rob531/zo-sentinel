import os
import time
import logging
import signal
import hashlib
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import requests

SERVICE_NAME = "score_run_ledger_writer"
SERVICE_PORT = 0
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/home/workspace/logs/{SERVICE_NAME}.log"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"
EXECUTE_SERVICE_URL = "http://localhost:8772"
HEARTBEAT_INTERVAL = 300
POLL_SECS = 300

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger(__name__)


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed for table={table}: {e}")
        return False


def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    try:
        resp = requests.post(f"{QUERY_SERVICE_URL}/query", json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return None


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(f"{EXECUTE_SERVICE_URL}/execute", json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


def check_single_instance() -> bool:
    pid_file = PID_FILE
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                old_pid = int(f.read().strip())
            if os.path.exists(f"/proc/{old_pid}"):
                log.warning(f"Another instance is running with PID {old_pid}")
                return False
        except (ValueError, IOError):
            pass
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file() -> None:
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def signal_handler(signum: int, frame) -> None:
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    raise SystemExit(0)


def send_heartbeat(status: str = "running", meta: Optional[Dict[str, Any]] = None) -> bool:
    ts = datetime.now(timezone.utc).isoformat()
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": ts,
        "status": status,
        "ts": ts,
        "meta": meta or {},
    }
    return ws_write("service_health", [row])


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def compute_run_id() -> str:
    ts = utc_now_iso()
    return hashlib.sha256(ts.encode()).hexdigest()[:16]


def ensure_score_run_table() -> None:
    create_sql = """
    CREATE TABLE IF NOT EXISTS score_run_ledger (
        run_id VARCHAR(64) PRIMARY KEY,
        started_at TIMESTAMPTZ NOT NULL,
        ended_at TIMESTAMPTZ,
        server_count INTEGER DEFAULT 0,
        servers_scored INTEGER DEFAULT 0,
        run_status VARCHAR(32) DEFAULT 'started',
        created_at TIMESTAMPTZ NOT NULL
    )
    """
    ws_execute(create_sql)


def get_recent_run_count() -> int:
    sql = "SELECT COUNT(*) as cnt FROM score_run_ledger WHERE created_at >= NOW() - INTERVAL '1 day'"
    rows = ws_query(sql)
    if rows and len(rows) > 0:
        return rows[0].get("cnt", 0)
    return 0


def get_scored_servers_in_window(window_minutes: int = 5) -> int:
    sql = f"""
    SELECT COUNT(DISTINCT server_id) as cnt 
    FROM mcp_signal_scores 
    WHERE computed_at >= NOW() - INTERVAL '{window_minutes} minutes'
    """
    rows = ws_query(sql)
    if rows and len(rows) > 0:
        return rows[0].get("cnt", 0)
    return 0


def record_score_run(run_id: str, started_at: str, server_count: int, servers_scored: int, status: str) -> bool:
    ended_at = utc_now_iso()
    row = {
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "server_count": server_count,
        "servers_scored": servers_scored,
        "run_status": status,
        "created_at": started_at,
    }
    return ws_write("score_run_ledger", [row])


def cycle() -> None:
    started_at = utc_now_iso()
    run_id = compute_run_id()

    recent_runs = get_recent_run_count()
    servers_scored = get_scored_servers_in_window(window_minutes=5)

    log.info(f"Score run {run_id}: {recent_runs} runs in last day, {servers_scored} servers scored in last 5 min")

    success = record_score_run(
        run_id=run_id,
        started_at=started_at,
        server_count=recent_runs,
        servers_scored=servers_scored,
        status="completed",
    )

    if success:
        log.info(f"Score run ledger entry recorded: run_id={run_id}")
    else:
        log.error(f"Failed to record score run ledger entry: run_id={run_id}")


def run() -> None:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    if not check_single_instance():
        log.error("Another instance is running. Exiting.")
        sys.exit(1)

    log.info(f"{SERVICE_NAME} starting")

    ensure_score_run_table()
    send_heartbeat(status="starting", meta={"phase": "initialized"})

    while True:
        try:
            cycle()
            send_heartbeat(status="running", meta={"poll_secs": POLL_SECS})
        except SystemExit:
            break
        except Exception as e:
            log.error(f"Error in cycle: {e}")
            send_heartbeat(status="error", meta={"error": str(e)})

        time.sleep(POLL_SECS)


if __name__ == "__main__":
    import sys
    try:
        run()
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        remove_pid_file()
        sys.exit(0)
    except Exception as e:
        log.error(f"Fatal error: {e}")
        remove_pid_file()
        sys.exit(1)