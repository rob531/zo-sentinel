import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler("/home/workspace/logs/verdict_transition_tracker_contract.log")],
)
log = logging.getLogger(__name__)

SERVICE_NAME = "verdict_transition_tracker_contract"
PORT = 8792
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
HEARTBEAT_INTERVAL = 60
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

app = FastAPI(title="Verdict Transition Tracker Contract")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed for table {table}: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


def send_heartbeat() -> None:
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": "running",
        "meta": "{}",
    }
    ws_write("service_health", [row])


def check_single_instance() -> None:
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error(f"Another instance is already running with PID {old_pid}")
            sys.exit(1)
        except (OSError, ValueError):
            log.warning(f"Stale PID file found at {PID_FILE}, removing it.")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum: int, frame: Any) -> None:
    log.info(f"Received signal {signum}, shutting down gracefully.")
    remove_pid_file()
    sys.exit(0)


VERDICT_STATES = ["TRUSTED", "AMBER", "UNTRUSTED", "UNKNOWN", "HIGH_RISK_ISOLATED", "CAUTION_LIMITED", "AMBER_UNVERIFIED", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED", "KNOWN_THREAT"]


def ensure_tables() -> None:
    create_sql = """
    CREATE TABLE IF NOT EXISTS verdict_transition_tracker (
        transition_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        previous_verdict VARCHAR,
        new_verdict VARCHAR NOT NULL,
        transition_type VARCHAR NOT NULL,
        transitioned_at TIMESTAMPTZ NOT NULL,
        confidence_before DOUBLE,
        confidence_after DOUBLE,
        reason VARCHAR,
        triggered_by VARCHAR,
        metadata_json VARCHAR,
        created_at TIMESTAMPTZ NOT NULL
    )
    """
    ws_execute(create_sql)
    log.info("verdict_transition_tracker table ensured.")


def compute_transition_id(server_id: str, transitioned_at: str, new_verdict: str) -> str:
    import hashlib
    raw = f"{server_id}:{transitioned_at}:{new_verdict}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def record_transition(
    server_id: str,
    previous_verdict: Optional[str],
    new_verdict: str,
    transition_type: str,
    transitioned_at: Optional[str] = None,
    confidence_before: Optional[float] = None,
    confidence_after: Optional[float] = None,
    reason: Optional[str] = None,
    triggered_by: Optional[str] = None,
    metadata_json: Optional[str] = None,
) -> bool:
    ts = transitioned_at or utc_now_iso()
    transition_id = compute_transition_id(server_id, ts, new_verdict)
    row = {
        "transition_id": transition_id,
        "server_id": server_id,
        "previous_verdict": previous_verdict,
        "new_verdict": new_verdict,
        "transition_type": transition_type,
        "transitioned_at": ts,
        "confidence_before": confidence_before,
        "confidence_after": confidence_after,
        "reason": reason,
        "triggered_by": triggered_by,
        "metadata_json": metadata_json,
        "created_at": utc_now_iso(),
    }
    return ws_write("verdict_transition_tracker", [row])


def get_transitions_for_server(server_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    sql = f"SELECT * FROM verdict_transition_tracker WHERE server_id = '{server_id}' ORDER BY transitioned_at DESC LIMIT {limit}"
    return ws_query(sql)


def get_transition_type_counts() -> List[Dict[str, Any]]:
    sql = """
    SELECT transition_type, new_verdict, COUNT(*) as count
    FROM verdict_transition_tracker
    GROUP BY transition_type, new_verdict
    ORDER BY count DESC
    """
    return ws_query(sql)


def get_recent_transitions(hours: int = 24, limit: int = 100) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT * FROM verdict_transition_tracker
    WHERE transitioned_at >= NOW() - INTERVAL '{hours} hours'
    ORDER BY transitioned_at DESC
    LIMIT {limit}
    """
    return ws_query(sql)


def get_transition_velocity(server_id: Optional[str] = None, days: int = 7) -> Dict[str, Any]:
    base_sql = f"""
    SELECT
        DATE(transitioned_at) as transition_date,
        COUNT(*) as transition_count,
        COUNT(DISTINCT server_id) as affected_servers
    FROM verdict_transition_tracker
    WHERE transitioned_at >= NOW() - INTERVAL '{days} days'
    """
    if server_id:
        base_sql += f" AND server_id = '{server_id}'"
    base_sql += " GROUP BY DATE(transitioned_at) ORDER BY transition_date"
    return {"daily": ws_query(base_sql)}


def get_transition_metadata_schema() -> Dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "version": "1.0.0",
        "verdict_states": VERDICT_STATES,
        "transition_types": [
            "initial_registration",
            "manual_review",
            "automated_scan",
            "threat_intel_update",
            "attestation_change",
            "signal_threshold_cross",
            "analyst_override",
            "exemption_granted",
            "exemption_revoked",
            "time_decay",
        ],
        "columns": [
            "transition_id",
            "server_id",
            "previous_verdict",
            "new_verdict",
            "transition_type",
            "transitioned_at",
            "confidence_before",
            "confidence_after",
            "reason",
            "triggered_by",
            "metadata_json",
            "created_at",
        ],
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME, "ts": utc_now_iso()}


@app.get("/contract/schema")
def contract_schema():
    return get_transition_metadata_schema()


@app.get("/transitions/server/{server_id}")
def transitions_by_server(server_id: str, limit: int = Query(default=50, le=500)):
    rows = get_transitions_for_server(server_id, limit=limit)
    return {"server_id": server_id, "count": len(rows), "transitions": rows}


@app.get("/transitions/recent")
def recent_transitions(hours: int = Query(default=24, le=168), limit: int = Query(default=100, le=500)):
    rows = get_recent_transitions(hours=hours, limit=limit)
    return {"hours": hours, "count": len(rows), "transitions": rows}


@app.get("/transitions/velocity")
def transition_velocity(
    server_id: Optional[str] = None,
    days: int = Query(default=7, le=30),
):
    return get_transition_velocity(server_id=server_id, days=days)


@app.get("/transitions/summary")
def transition_summary():
    rows = get_transition_type_counts()
    return {"count": len(rows), "breakdown": rows}


@app.post("/transitions/record")
def record_transition_endpoint(
    server_id: str,
    new_verdict: str,
    transition_type: str,
    previous_verdict: Optional[str] = None,
    transitioned_at: Optional[str] = None,
    confidence_before: Optional[float] = None,
    confidence_after: Optional[float] = None,
    reason: Optional[str] = None,
    triggered_by: Optional[str] = None,
    metadata_json: Optional[str] = None,
):
    if new_verdict not in VERDICT_STATES:
        raise HTTPException(status_code=400, detail=f"Invalid new_verdict: {new_verdict}")
    if transition_type not in get_transition_metadata_schema()["transition_types"]:
        raise HTTPException(status_code=400, detail=f"Invalid transition_type: {transition_type}")
    success = record_transition(
        server_id=server_id,
        previous_verdict=previous_verdict,
        new_verdict=new_verdict,
        transition_type=transition_type,
        transitioned_at=transitioned_at,
        confidence_before=confidence_before,
        confidence_after=confidence_after,
        reason=reason,
        triggered_by=triggered_by,
        metadata_json=metadata_json,
    )
    if success:
        return {"ok": True, "server_id": server_id, "new_verdict": new_verdict}
    raise HTTPException(status_code=500, detail="Failed to record transition")


@app.get("/")
def root():
    return {
        "service": SERVICE_NAME,
        "description": "Verdict Transition Tracker Contract API",
        "endpoints": [
            "GET /health",
            "GET /contract/schema",
            "GET /transitions/server/{server_id}",
            "GET /transitions/recent",
            "GET /transitions/velocity",
            "GET /transitions/summary",
            "POST /transitions/record",
        ],
    }


def run():
    import signal

    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    ensure_tables()
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    run()