import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

SERVICE_NAME = "forensic_detail_api_v2"
SERVICE_PORT = 8779
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(__name__)

app = FastAPI(title="Forensic Detail API v2", version="2.0.0")


def ws_query(sql: str, params: Optional[list] = None) -> list:
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_write(table: str, rows: list) -> None:
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()


def ws_execute(sql: str) -> None:
    payload = {"sql": sql}
    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
    resp.raise_for_status()


def check_single_instance():
    if os.path.exists(PID_FILE):
        with open(PID_FILE, "r") as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error(f"Another instance is running with PID {old_pid}")
            sys.exit(1)
        except (OSError, ValueError):
            log.warning(f"Stale PID file found, removing it")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def send_heartbeat(status: str = "running", meta: Optional[dict] = None) -> None:
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": status,
        "meta": meta or {}
    }
    try:
        ws_write("service_health", [row])
    except Exception as e:
        log.warning(f"Failed to send heartbeat: {e}")


def heartbeat_loop():
    send_heartbeat("running", {"port": SERVICE_PORT, "version": "2.0.0"})


def ensure_tables():
    sql = """
    CREATE TABLE IF NOT EXISTS forensic_cache (
        server_id VARCHAR NOT NULL,
        cache_key VARCHAR NOT NULL,
        cache_value TEXT,
        computed_at TIMESTAMPTZ,
        PRIMARY KEY (server_id, cache_key)
    )
    """
    try:
        ws_execute(sql)
        log.info("Ensured forensic_cache table exists")
    except Exception as e:
        log.warning(f"Table creation skipped (may already exist): {e}")


def get_registry_record(server_id: str) -> Optional[dict]:
    sql = """
    SELECT server_id, name, url, description, trust_score, verdict, registry_source, scan_count, first_seen, last_seen
    FROM mcp_server_registry
    WHERE server_id = ?
    """
    rows = ws_query(sql, [server_id])
    return rows[0] if rows else None


def get_signal_scores(server_id: str) -> list:
    sql = """
    SELECT server_id, signal_name, score, evidence, scored_at
    FROM mcp_signal_scores
    WHERE server_id = ?
    ORDER BY signal_name
    """
    return ws_query(sql, [server_id])


def get_threat_associations(server_id: str) -> list:
    sql = """
    SELECT server_id, threat_type, severity, evidence, reported_at
    FROM mcp_threat_associations
    WHERE server_id = ?
    ORDER BY reported_at DESC
    """
    return ws_query(sql, [server_id])


def get_attestation_history(server_id: str) -> list:
    sql = """
    SELECT server_id, attestation_type, attested_by, attested_at, evidence, expiry_date, status
    FROM mcp_attestations
    WHERE server_id = ?
    ORDER BY attested_at DESC
    """
    return ws_query(sql, [server_id])


def get_verdict_history(server_id: str) -> list:
    sql = """
    SELECT server_id, verdict, risk_tier, risk_rank, computed_at, reasoning
    FROM mcp_risk_register
    WHERE server_id = ?
    ORDER BY computed_at DESC
    """
    return ws_query(sql, [server_id])


def get_audit_events(server_id: str, limit: int = 50) -> list:
    sql = """
    SELECT id, target_server_id, event_type, actor, detail, created_at
    FROM audit_log
    WHERE target_server_id = ?
    ORDER BY created_at DESC
    LIMIT ?
    """
    return ws_query(sql, [server_id, limit])


@app.get("/servers/{server_id}/forensic")
def get_server_forensic(
    server_id: str,
    include_signals: bool = Query(True, description="Include signal scores"),
    include_threats: bool = Query(True, description="Include threat associations"),
    include_attestations: bool = Query(True, description="Include attestation history"),
    include_verdicts: bool = Query(False, description="Include verdict history"),
    include_audit: bool = Query(False, description="Include audit trail")
):
    """
    Get comprehensive forensic data for a specific MCP server.
    Returns signal scores, threat associations, attestation history, and more.
    """
    start_time = time.time()
    registry = get_registry_record(server_id)
    
    if not registry:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found in registry")
    
    result = {
        "server_id": server_id,
        "registry": registry,
        "fetched_at": utc_now_iso(),
        "signals": [],
        "threats": [],
        "attestations": [],
        "verdicts": [],
        "audit_events": []
    }
    
    if include_signals:
        result["signals"] = get_signal_scores(server_id)
    
    if include_threats:
        result["threats"] = get_threat_associations(server_id)
    
    if include_attestations:
        result["attestations"] = get_attestation_history(server_id)
    
    if include_verdicts:
        result["verdicts"] = get_verdict_history(server_id)
    
    if include_audit:
        result["audit_events"] = get_audit_events(server_id, limit=50)
    
    result["query_time_ms"] = round((time.time() - start_time) * 1000, 2)
    
    return JSONResponse(content=result)


@app.get("/servers/{server_id}/signals")
def get_server_signals(server_id: str):
    """Get only signal scores for a server."""
    registry = get_registry_record(server_id)
    if not registry:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    signals = get_signal_scores(server_id)
    
    signal_summary = {}
    for sig in signals:
        signal_summary[sig["signal_name"]] = {
            "score": sig["score"],
            "scored_at": sig["scored_at"]
        }
    
    return JSONResponse(content={
        "server_id": server_id,
        "signal_count": len(signals),
        "signals": signal_summary
    })


@app.get("/servers/{server_id}/threats")
def get_server_threats(server_id: str):
    """Get only threat associations for a server."""
    registry = get_registry_record(server_id)
    if not registry:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    threats = get_threat_associations(server_id)
    
    return JSONResponse(content={
        "server_id": server_id,
        "threat_count": len(threats),
        "threats": threats
    })


@app.get("/servers/{server_id}/attestations")
def get_server_attestations(server_id: str):
    """Get only attestation history for a server."""
    registry = get_registry_record(server_id)
    if not registry:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    attestations = get_attestation_history(server_id)
    
    return JSONResponse(content={
        "server_id": server_id,
        "attestation_count": len(attestations),
        "attestations": attestations
    })


@app.get("/health")
def health():
    """Health check endpoint."""
    try:
        ws_query("SELECT 1", [])
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return JSONResponse(content={
        "status": "ok",
        "service": SERVICE_NAME,
        "port": SERVICE_PORT,
        "db_status": db_status,
        "timestamp": utc_now_iso()
    })


@app.get("/")
def root():
    """Root endpoint with API information."""
    return JSONResponse(content={
        "service": SERVICE_NAME,
        "version": "2.0.0",
        "port": SERVICE_PORT,
        "endpoints": [
            "GET /servers/{server_id}/forensic - Full forensic report",
            "GET /servers/{server_id}/signals - Signal scores only",
            "GET /servers/{server_id}/threats - Threat associations only",
            "GET /servers/{server_id}/attestations - Attestation history only",
            "GET /health - Health check"
        ]
    })


def run():
    """Main entry point for the service."""
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    os.makedirs(LOG_DIR, exist_ok=True)
    
    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    
    ensure_tables()
    
    send_heartbeat("starting", {"port": SERVICE_PORT})
    
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT, log_level="info")


if __name__ == "__main__":
    run()