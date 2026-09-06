import os
import sys
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel

SERVICE_NAME = "risk_tier_transitions_logic"
SERVICE_PORT = 8791
WRITE_SERVICE_URL = os.environ.get("WRITE_SERVICE_URL", "http://localhost:8772")
QUERY_SERVICE_URL = os.environ.get("QUERY_SERVICE_URL", "http://localhost:8772/query")
EXECUTE_SERVICE_URL = os.environ.get("EXECUTE_SERVICE_URL", "http://localhost:8772/execute")
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(__name__)

app = FastAPI(title=f"{SERVICE_NAME}", version="1.0.0")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    try:
        payload = {"sql": sql}
        if params:
            payload["params"] = params
        resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed: {e}")
        return False


def ws_execute(sql: str, params: Optional[List[Any]] = None) -> bool:
    try:
        payload = {"sql": sql}
        if params:
            payload["params"] = params
        resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


def ensure_tables() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS risk_tier_transitions (
        transition_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        from_tier VARCHAR,
        to_tier VARCHAR NOT NULL,
        transition_reason VARCHAR,
        triggered_by VARCHAR,
        transitioned_at TIMESTAMPTZ NOT NULL,
        metadata JSON
    )
    """
    ws_execute(sql)
    sql_idx = """
    CREATE INDEX IF NOT EXISTS idx_rtt_server_id ON risk_tier_transitions(server_id)
    """
    ws_execute(sql_idx)
    sql_idx2 = """
    CREATE INDEX IF NOT EXISTS idx_rtt_transitioned_at ON risk_tier_transitions(transitioned_at)
    """
    ws_execute(sql_idx2)
    log.info("Ensured risk_tier_transitions table exists")


def send_heartbeat(status: str = "running", meta: Optional[Dict[str, Any]] = None) -> None:
    row = {
        "service": SERVICE_NAME,
        "status": status,
        "last_heartbeat": utc_now_iso(),
        "meta": meta or {}
    }
    ws_write("service_health", [row])


def compute_transition_id(server_id: str, to_tier: str, transitioned_at: str) -> str:
    import hashlib
    raw = f"{server_id}:{to_tier}:{transitioned_at}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def get_current_tier(server_id: str) -> Optional[str]:
    sql = """
    SELECT risk_tier FROM mcp_risk_register
    WHERE server_id = ?
    ORDER BY computed_at DESC
    LIMIT 1
    """
    rows = ws_query(sql, [server_id])
    if rows:
        return rows[0].get("risk_tier")
    sql2 = """
    SELECT verdict FROM mcp_server_registry
    WHERE server_id = ?
    LIMIT 1
    """
    rows2 = ws_query(sql2, [server_id])
    if rows2:
        return rows2[0].get("verdict")
    return None


def record_transition(
    server_id: str,
    to_tier: str,
    transition_reason: Optional[str] = None,
    triggered_by: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    from_tier = get_current_tier(server_id)
    transitioned_at = utc_now_iso()
    transition_id = compute_transition_id(server_id, to_tier, transitioned_at)
    
    row = {
        "transition_id": transition_id,
        "server_id": server_id,
        "from_tier": from_tier,
        "to_tier": to_tier,
        "transition_reason": transition_reason,
        "triggered_by": triggered_by,
        "transitioned_at": transitioned_at,
        "metadata": metadata or {}
    }
    
    if ws_write("risk_tier_transitions", [row]):
        log.info(f"Recorded transition {transition_id}: {server_id} {from_tier} -> {to_tier}")
        return transition_id
    return None


def get_transitions_for_server(
    server_id: str,
    limit: int = 50,
    offset: int = 0
) -> List[Dict[str, Any]]:
    sql = """
    SELECT * FROM risk_tier_transitions
    WHERE server_id = ?
    ORDER BY transitioned_at DESC
    LIMIT ? OFFSET ?
    """
    return ws_query(sql, [server_id, limit, offset])


def get_recent_transitions(limit: int = 100) -> List[Dict[str, Any]]:
    sql = """
    SELECT * FROM risk_tier_transitions
    ORDER BY transitioned_at DESC
    LIMIT ?
    """
    return ws_query(sql, [limit])


def get_transition_counts_by_tier(days: int = 30) -> Dict[str, Any]:
    sql = """
    SELECT 
        from_tier,
        to_tier,
        COUNT(*) as count
    FROM risk_tier_transitions
    WHERE transitioned_at >= CURRENT_TIMESTAMP - INTERVAL '1 day' * ?
    GROUP BY from_tier, to_tier
    ORDER BY count DESC
    """
    return ws_query(sql, [days])


def get_transition_velocity(days: int = 7) -> Dict[str, Any]:
    sql = """
    SELECT 
        DATE(transitioned_at) as date,
        COUNT(*) as transitions
    FROM risk_tier_transitions
    WHERE transitioned_at >= CURRENT_TIMESTAMP - INTERVAL '1 day' * ?
    GROUP BY DATE(transitioned_at)
    ORDER BY date DESC
    """
    return ws_query(sql, [days])


def get_servers_in_transition(threshold_hours: int = 24) -> List[Dict[str, Any]]:
    sql = """
    SELECT DISTINCT ON (server_id)
        server_id,
        from_tier,
        to_tier,
        transitioned_at,
        transition_reason
    FROM risk_tier_transitions
    WHERE transitioned_at >= CURRENT_TIMESTAMP - INTERVAL '1 hour' * ?
    ORDER BY server_id, transitioned_at DESC
    """
    return ws_query(sql, [threshold_hours])


class TransitionRecordRequest(BaseModel):
    server_id: str
    to_tier: str
    transition_reason: Optional[str] = None
    triggered_by: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class TransitionQueryParams(BaseModel):
    server_id: Optional[str] = None
    from_tier: Optional[str] = None
    to_tier: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None
    limit: int = 100
    offset: int = 0


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME, "uptime": "running"}


@app.get("/api/transitions")
def list_transitions(
    server_id: Optional[str] = Query(None),
    from_tier: Optional[str] = Query(None),
    to_tier: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    conditions = []
    params = []
    
    if server_id:
        conditions.append("server_id = ?")
        params.append(server_id)
    if from_tier:
        conditions.append("from_tier = ?")
        params.append(from_tier)
    if to_tier:
        conditions.append("to_tier = ?")
        params.append(to_tier)
    if since:
        conditions.append("transitioned_at >= ?")
        params.append(since)
    if until:
        conditions.append("transitioned_at <= ?")
        params.append(until)
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    sql = f"""
    SELECT * FROM risk_tier_transitions
    WHERE {where_clause}
    ORDER BY transitioned_at DESC
    LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    
    rows = ws_query(sql, params)
    return {"transitions": rows, "count": len(rows)}


@app.get("/api/transitions/server/{server_id}")
def get_server_transitions(
    server_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    return get_transitions_for_server(server_id, limit, offset)


@app.post("/api/transitions")
def create_transition(req: TransitionRecordRequest):
    transition_id = record_transition(
        server_id=req.server_id,
        to_tier=req.to_tier,
        transition_reason=req.transition_reason,
        triggered_by=req.triggered_by,
        metadata=req.metadata
    )
    if transition_id:
        return {"success": True, "transition_id": transition_id}
    raise HTTPException(status_code=500, detail="Failed to record transition")


@app.get("/api/transitions/stats")
def get_transition_stats(days: int = Query(30, ge=1, le=365)):
    by_tier = get_transition_counts_by_tier(days)
    velocity = get_transition_velocity(days)
    in_transition = get_servers_in_transition(24)
    
    sql_total = """
    SELECT COUNT(*) as total FROM risk_tier_transitions
    WHERE transitioned_at >= CURRENT_TIMESTAMP - INTERVAL '1 day' * ?
    """
    total_rows = ws_query(sql_total, [days])
    total = total_rows[0]["total"] if total_rows else 0
    
    return {
        "period_days": days,
        "total_transitions": total,
        "by_tier_pair": by_tier,
        "velocity": velocity,
        "servers_in_transition_24h": len(in_transition)
    }


@app.get("/api/transitions/velocity")
def get_velocity(days: int = Query(7, ge=1, le=90)):
    return {"velocity": get_transition_velocity(days)}


def check_single_instance():
    if os.path.exists(PID_FILE):
        with open(PID_FILE, "r") as f:
            old_pid = f.read().strip()
        if old_pid and os.path.exists(f"/proc/{old_pid}"):
            log.error(f"Already running as PID {old_pid}")
            sys.exit(1)
        else:
            os.remove(PID_FILE)
    
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def run():
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    check_single_instance()
    ensure_tables()
    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT, log_level="info")


if __name__ == "__main__":
    run()