import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

SERVICE_NAME = "perspective_snapshot_api"
SERVICE_PORT = 8791
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
PID_FILE = "/tmp/perspective_snapshot_api.pid"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = os.path.join(LOG_DIR, f"{SERVICE_NAME}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger(SERVICE_NAME)


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL + "/write",
            json={"table": table, "rows": rows},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_write failed for table %s: %s", table, e)
        return False


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_URL,
            json={"sql": sql},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error("ws_query failed: %s | SQL: %s", e, sql[:200])
        return []


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_URL,
            json={"sql": sql},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_execute failed: %s | SQL: %s", e, sql[:200])
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_snapshot_id(server_id: str, perspective: str, ts: str) -> str:
    import hashlib
    raw = f"{server_id}:{perspective}:{ts}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


app = FastAPI(title=SERVICE_NAME, version="1.0.0")
_start_time = time.time()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime": int(time.time() - _start_time),
    }


@app.get("/api/perspective/snapshot")
def get_perspective_snapshot(
    server_id: Optional[str] = Query(None, description="Filter by server_id"),
    perspective: Optional[str] = Query(
        None,
        description="Perspective label: analyst, automated, audit, trend, realtime",
    ),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """
    Retrieve perspective snapshot records from mcp_perspective_snapshots.
    Columns: snapshot_id, server_id, perspective, trust_score, risk_tier,
             verdict, computed_at, meta (JSON evidence blob)
    """
    conditions = []
    params = {}

    if server_id:
        conditions.append("server_id = :server_id")
        params["server_id"] = server_id
    if perspective:
        conditions.append("perspective = :perspective")
        params["perspective"] = perspective

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"""
        SELECT snapshot_id, server_id, perspective, trust_score, risk_tier,
               verdict, computed_at, meta
        FROM mcp_perspective_snapshots
        {where_clause}
        ORDER BY computed_at DESC
        LIMIT :limit OFFSET :offset
    """
    params["limit"] = limit
    params["offset"] = offset

    rows = ws_query(sql)
    return JSONResponse({"rows": rows, "count": len(rows)})


@app.get("/api/perspective/snapshot/{snapshot_id}")
def get_perspective_snapshot_by_id(snapshot_id: str):
    sql = f"""
        SELECT snapshot_id, server_id, perspective, trust_score, risk_tier,
               verdict, computed_at, meta
        FROM mcp_perspective_snapshots
        WHERE snapshot_id = :snapshot_id
    """
    rows = ws_query(sql)
    if not rows:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return JSONResponse({"row": rows[0]})


@app.post("/api/perspective/snapshot")
def create_perspective_snapshot(
    server_id: str = Query(...),
    perspective: str = Query(...),
    trust_score: float = Query(..., ge=0.0, le=100.0),
    risk_tier: Optional[str] = Query(None),
    verdict: Optional[str] = Query(None),
    meta: Optional[Dict[str, Any]] = Query(default_factory=dict),
):
    """
    Write a perspective snapshot record to mcp_perspective_snapshots.
    """
    ts = utc_now_iso()
    snapshot_id = compute_snapshot_id(server_id, perspective, ts)

    import json
    row = {
        "snapshot_id": snapshot_id,
        "server_id": server_id,
        "perspective": perspective,
        "trust_score": trust_score,
        "risk_tier": risk_tier,
        "verdict": verdict,
        "computed_at": ts,
        "meta": json.dumps(meta) if isinstance(meta, dict) else meta,
    }
    success = ws_write("mcp_perspective_snapshots", [row])
    if not success:
        raise HTTPException(status_code=500, detail="Failed to write snapshot")
    return JSONResponse({"snapshot_id": snapshot_id, "created_at": ts})


@app.get("/api/perspective/trend")
def get_perspective_trend(
    server_id: str = Query(...),
    perspective: str = Query(...),
    days: int = Query(30, ge=1, le=365),
):
    """
    Return trust_score trend over the last N days for a server + perspective.
    """
    sql = f"""
        SELECT trust_score, computed_at
        FROM mcp_perspective_snapshots
        WHERE server_id = :server_id
          AND perspective = :perspective
          AND computed_at >= NOW() - INTERVAL '{days} days'
        ORDER BY computed_at ASC
    """
    rows = ws_query(sql)
    return JSONResponse({
        "server_id": server_id,
        "perspective": perspective,
        "days": days,
        "data_points": rows,
        "count": len(rows),
    })


@app.get("/api/perspective/contrast")
def get_perspective_contrast(
    server_id: str = Query(...),
):
    """
    Return side-by-side perspectives for a server — analyst vs automated vs trend.
    """
    sql = f"""
        SELECT perspective, trust_score, risk_tier, verdict, computed_at
        FROM mcp_perspective_snapshots s1
        WHERE server_id = :server_id
          AND computed_at = (
              SELECT MAX(s2.computed_at)
              FROM mcp_perspective_snapshots s2
              WHERE s2.server_id = s1.server_id
                AND s2.perspective = s1.perspective
          )
        ORDER BY perspective
    """
    rows = ws_query(sql)
    return JSONResponse({
        "server_id": server_id,
        "perspectives": rows,
        "count": len(rows),
    })


def ensure_tables() -> bool:
    sql = f"""
        CREATE TABLE IF NOT EXISTS mcp_perspective_snapshots (
            snapshot_id  VARCHAR PRIMARY KEY,
            server_id    VARCHAR NOT NULL,
            perspective  VARCHAR NOT NULL,
            trust_score  DOUBLE NOT NULL,
            risk_tier    VARCHAR,
            verdict      VARCHAR,
            computed_at  TIMESTAMPTZ NOT NULL,
            meta         JSON
        )
    """
    return ws_execute(sql)


def check_single_instance() -> bool:
    import os
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = f.read().strip()
        try:
            import subprocess
            subprocess.run(["ps", "-p", old_pid], capture_output=True, check=True)
            log.error("Another instance already running with PID %s", old_pid)
            return False
        except Exception:
            log.warning("Stale PID file %s — removing", PID_FILE)
            os.remove(PID_FILE)
    pid = str(os.getpid())
    with open(PID_FILE, "w") as f:
        f.write(pid)
    log.info("PID %s written to %s", pid, PID_FILE)
    return True


def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


def signal_handler(signum, frame):
    log.info("Received signal %d — shutting down", signum)
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    ts = utc_now_iso()
    ws_write("service_health", [{
        "service": SERVICE_NAME,
        "last_heartbeat": ts,
        "status": "running",
        "meta": "{}",
    }])


def heartbeat_loop():
    send_heartbeat()


def run():
    log.info("Starting %s on port %d", SERVICE_NAME, SERVICE_PORT)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    if not check_single_instance():
        sys.exit(1)
    ensure_tables()
    send_heartbeat()
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)


if __name__ == "__main__":
    run()