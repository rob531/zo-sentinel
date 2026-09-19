import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query

# Constants
SERVICE_NAME = "verdict_watchlist_service"
PORT = 8786
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = Path("/home/workspace/logs")
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

app = FastAPI()
_start_time = time.time()


def ws_write(table: str, rows: list) -> dict:
    """Write rows to write_service."""
    resp = requests.post(
        WRITE_SERVICE_URL,
        json={"table": table, "rows": rows, "wait": True},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str) -> list:
    """Query write_service."""
    resp = requests.post(
        QUERY_URL,
        json={"sql": sql},
        timeout=30
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get("rows", [])


def ws_execute(sql: str) -> dict:
    """Execute SQL on write_service (DDL/DML)."""
    resp = requests.post(
        EXECUTE_URL,
        json={"sql": sql},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_single_instance():
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        old_pid = int(pid_path.read_text().strip())
        try:
            os.kill(old_pid, 0)
            logger.error(f"{SERVICE_NAME} already running as PID {old_pid}")
            sys.exit(1)
        except OSError:
            pass
    pid_path.write_text(str(os.getpid()))


def remove_pid_file():
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def ensure_tables():
    sql = """
    CREATE TABLE IF NOT EXISTS verdict_watchlist (
        entry_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        reason VARCHAR NOT NULL,
        added_by VARCHAR NOT NULL,
        added_at TIMESTAMPTZ NOT NULL,
        status VARCHAR NOT NULL DEFAULT 'active',
        last_updated TIMESTAMPTZ,
        notes VARCHAR
    )
    """
    ws_execute(sql)


def send_heartbeat():
    ts = utc_now_iso()
    rows = [{"service": SERVICE_NAME, "last_heartbeat": ts, "status": "ok", "meta": "{}"}]
    try:
        ws_write("service_health", rows)
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


@app.on_event("startup")
async def startup():
    check_single_instance()
    ensure_tables()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    logger.info(f"{SERVICE_NAME} started on port {PORT}")


@app.get("/health")
async def health():
    uptime = int(time.time() - _start_time)
    return {"status": "ok", "service": SERVICE_NAME, "uptime": uptime}


@app.get("/watchlist")
async def get_watchlist(
    server_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100)
):
    conditions = []
    params = []
    if server_id:
        conditions.append("server_id = ?")
        params.append(server_id)
    if status:
        conditions.append("status = ?")
        params.append(status)
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT * FROM verdict_watchlist WHERE {where_clause} ORDER BY added_at DESC LIMIT {limit}"
    rows = ws_query(sql)
    return {"entries": rows, "count": len(rows)}


@app.post("/watchlist")
async def add_to_watchlist(
    server_id: str,
    reason: str,
    added_by: str,
    notes: Optional[str] = None
):
    entry_id = f"wl_{server_id}_{int(time.time() * 1000)}"
    added_at = utc_now_iso()
    sql_check = f"SELECT entry_id FROM verdict_watchlist WHERE server_id = '{server_id}' AND status = 'active'"
    existing = ws_query(sql_check)
    if existing:
        raise HTTPException(status_code=409, detail=f"Server {server_id} already on active watchlist")
    sql = f"INSERT INTO verdict_watchlist (entry_id, server_id, reason, added_by, added_at, status, notes) VALUES ('{entry_id}', '{server_id}', '{reason}', '{added_by}', '{added_at}', 'active', {repr(notes) if notes else 'NULL'})"
    ws_execute(sql)
    return {"entry_id": entry_id, "status": "added", "server_id": server_id}


@app.delete("/watchlist/{server_id}")
async def remove_from_watchlist(server_id: str):
    sql = f"DELETE FROM verdict_watchlist WHERE server_id = '{server_id}'"
    result = ws_execute(sql)
    return {"status": "removed", "server_id": server_id}


@app.patch("/watchlist/{server_id}")
async def update_watchlist_entry(
    server_id: str,
    status: Optional[str] = None,
    notes: Optional[str] = None
):
    sql_check = f"SELECT entry_id, status FROM verdict_watchlist WHERE server_id = '{server_id}'"
    existing = ws_query(sql_check)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found in watchlist")
    updates = []
    if status:
        updates.append(f"status = '{status}'")
    if notes is not None:
        updates.append(f"notes = {repr(notes)}")
    updates.append(f"last_updated = '{utc_now_iso()}'")
    sql = f"UPDATE verdict_watchlist SET {', '.join(updates)} WHERE server_id = '{server_id}'"
    ws_execute(sql)
    return {"status": "updated", "server_id": server_id}


@app.get("/watchlist/check/{server_id}")
async def check_watchlist(server_id: str):
    sql = f"SELECT entry_id, status, reason, added_at FROM verdict_watchlist WHERE server_id = '{server_id}' AND status = 'active'"
    rows = ws_query(sql)
    if rows:
        entry = rows[0]
        return {"on_watchlist": True, "entry": entry}
    return {"on_watchlist": False}


@app.get("/watchlist/stats")
async def get_watchlist_stats():
    sql = "SELECT status, COUNT(*) as count FROM verdict_watchlist GROUP BY status"
    rows = ws_query(sql)
    total_sql = "SELECT COUNT(*) as total FROM verdict_watchlist"
    total_rows = ws_query(total_sql)
    total = total_rows[0]["total"] if total_rows else 0
    return {"total": total, "by_status": rows}


def run():
    send_heartbeat()
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    run()