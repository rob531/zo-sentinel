import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

WRITE_SERVICE_URL = "http://localhost:8772"
SERVICE_NAME = "admin_submissions_view"
PORT = 8790
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

app = FastAPI(title="Admin Submissions View API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def ws_query(sql: str, params: Optional[dict] = None) -> list:
    """Query DuckDB via write_service."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL + "/query",
            json={"sql": sql, "params": params or {}},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except requests.exceptions.RequestException as e:
        log.error(f"ws_query failed: {e}")
        raise HTTPException(status_code=503, detail="Database unavailable")


def ws_write(table: str, rows: list) -> dict:
    """Write to DuckDB via write_service."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL + "/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        log.error(f"ws_write failed: {e}")
        raise HTTPException(status_code=503, detail="Write failed")


def ws_execute(sql: str, params: Optional[dict] = None) -> dict:
    """Execute DDL/DML via write_service."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL + "/execute",
            json={"sql": sql, "params": params or {}},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        log.error(f"ws_execute failed: {e}")
        raise HTTPException(status_code=503, detail="Execution failed")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_tables():
    """Create required tables if they don't exist."""
    create_submissions_audit = """
    CREATE SEQUENCE IF NOT EXISTS submissions_audit_id_seq
    """
    ws_execute(create_submissions_audit)
    
    create_audit = """
    CREATE TABLE IF NOT EXISTS admin_submissions_audit (
        id INTEGER DEFAULT nextval('submissions_audit_id_seq'),
        server_id VARCHAR,
        action VARCHAR,
        actor VARCHAR,
        details_json VARCHAR,
        timestamp TIMESTAMPTZ,
        immutable BOOLEAN DEFAULT false
    )
    """
    ws_execute(create_audit)


@app.on_event("startup")
async def startup():
    log.info(f"Starting {SERVICE_NAME}")
    ensure_tables()


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME, "timestamp": utc_now_iso()}


@app.get("/api/submissions")
async def list_submissions(
    status: Optional[str] = Query(None, description="Filter by submission status"),
    verdict: Optional[str] = Query(None, description="Filter by verdict"),
    source: Optional[str] = Query(None, description="Filter by registry source"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("last_seen", description="Sort field"),
    sort_order: str = Query("DESC", pattern="^(ASC|DESC)$"),
):
    """List all MCP submissions with optional filters."""
    valid_sort_fields = [
        "server_id", "name", "trust_score", "verdict", "registry_source",
        "scan_count", "first_seen", "last_seen", "last_scanned", "last_assessed"
    ]
    if sort_by not in valid_sort_fields:
        sort_by = "last_seen"
    
    sql = f"""
    SELECT 
        server_id,
        name,
        url,
        description,
        trust_score,
        verdict,
        registry_source,
        scan_count,
        first_seen,
        last_seen,
        last_scanned,
        last_assessed
    FROM mcp_server_registry
    WHERE 1=1
    """
    params = {}
    
    if status:
        sql += " AND verdict = %(verdict)s"
        params["verdict"] = status
    
    if verdict:
        sql += " AND verdict = %(verdict)s"
        params["verdict"] = verdict
    
    if source:
        sql += " AND registry_source = %(source)s"
        params["source"] = source
    
    sql += f" ORDER BY {sort_by} {sort_order}"
    sql += " LIMIT %(limit)s OFFSET %(offset)s"
    params["limit"] = limit
    params["offset"] = offset
    
    rows = ws_query(sql, params)
    
    count_sql = "SELECT COUNT(*) as total FROM mcp_server_registry WHERE 1=1"
    count_params = {}
    if status:
        count_sql += " AND verdict = %(verdict)s"
        count_params["verdict"] = status
    if verdict:
        count_sql += " AND verdict = %(verdict)s"
        count_params["verdict"] = verdict
    if source:
        count_sql += " AND registry_source = %(source)s"
        count_params["source"] = source
    
    count_result = ws_query(count_sql, count_params)
    total = count_result[0]["total"] if count_result else 0
    
    return {
        "submissions": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
        "filters": {
            "status": status,
            "verdict": verdict,
            "source": source,
        }
    }


@app.get("/api/submissions/{server_id}")
async def get_submission_detail(server_id: str):
    """Get detailed information for a specific submission."""
    sql = """
    SELECT 
        server_id,
        name,
        url,
        description,
        trust_score,
        verdict,
        registry_source,
        scan_count,
        first_seen,
        last_seen,
        last_scanned,
        last_assessed
    FROM mcp_server_registry
    WHERE server_id = %(server_id)s
    """
    rows = ws_query(sql, {"server_id": server_id})
    
    if not rows:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    submission = rows[0]
    
    signals_sql = """
    SELECT 
        signal_name,
        score,
        evidence,
        scored_at
    FROM mcp_signal_scores
    WHERE server_id = %(server_id)s
    ORDER BY signal_name
    """
    signals = ws_query(signals_sql, {"server_id": server_id})
    
    threat_sql = """
    SELECT 
        threat_type,
        severity,
        evidence,
        reported_at
    FROM mcp_threat_associations
    WHERE server_id = %(server_id)s
    ORDER BY reported_at DESC
    """
    threats = ws_query(threat_sql, {"server_id": server_id})
    
    audit_sql = """
    SELECT 
        id,
        action,
        actor,
        details_json,
        timestamp
    FROM admin_submissions_audit
    WHERE server_id = %(server_id)s
    ORDER BY timestamp DESC
    LIMIT 20
    """
    audit_trail = ws_query(audit_sql, {"server_id": server_id})
    
    return {
        "submission": submission,
        "signals": signals,
        "threats": threats,
        "audit_trail": audit_trail,
    }


@app.get("/api/submissions/{server_id}/signals")
async def get_submission_signals(server_id: str):
    """Get all signal scores for a submission."""
    sql = """
    SELECT 
        signal_name,
        score,
        evidence,
        scored_at
    FROM mcp_signal_scores
    WHERE server_id = %(server_id)s
    ORDER BY signal_name
    """
    rows = ws_query(sql, {"server_id": server_id})
    return {"server_id": server_id, "signals": rows}


@app.get("/api/submissions/stats")
async def get_submission_stats():
    """Get aggregated statistics about submissions."""
    verdict_dist_sql = """
    SELECT 
        verdict,
        COUNT(*) as count
    FROM mcp_server_registry
    GROUP BY verdict
    ORDER BY count DESC
    """
    verdict_dist = ws_query(verdict_dist_sql)
    
    source_dist_sql = """
    SELECT 
        registry_source,
        COUNT(*) as count
    FROM mcp_server_registry
    GROUP BY registry_source
    ORDER BY count DESC
    """
    source_dist = ws_query(source_dist_sql)
    
    recent_submissions_sql = """
    SELECT COUNT(*) as count
    FROM mcp_server_registry
    WHERE last_seen >= NOW() - INTERVAL '7 days'
    """
    recent_result = ws_query(recent_submissions_sql)
    recent_count = recent_result[0]["count"] if recent_result else 0
    
    total_sql = "SELECT COUNT(*) as total FROM mcp_server_registry"
    total_result = ws_query(total_sql)
    total = total_result[0]["total"] if total_result else 0
    
    avg_trust_sql = """
    SELECT AVG(trust_score) as avg_trust
    FROM mcp_server_registry
    WHERE trust_score IS NOT NULL
    """
    avg_result = ws_query(avg_trust_sql)
    avg_trust = float(avg_result[0]["avg_trust"]) if avg_result and avg_result[0].get("avg_trust") is not None else 0.0
    
    return {
        "total_submissions": total,
        "recent_submissions_7d": recent_count,
        "average_trust_score": round(avg_trust, 2),
        "verdict_distribution": verdict_dist,
        "source_distribution": source_dist,
    }


@app.post("/api/submissions/{server_id}/audit")
async def record_submission_action(
    server_id: str,
    action: str = Query(..., description="Action performed"),
    actor: str = Query("system", description="Who performed the action"),
    details: Optional[str] = Query(None, description="Additional details as JSON string"),
):
    """Record an audit event for a submission action."""
    timestamp = utc_now_iso()
    
    ws_write("admin_submissions_audit", [{
        "server_id": server_id,
        "action": action,
        "actor": actor,
        "details_json": details,
        "timestamp": timestamp,
        "immutable": True,
    }])
    
    log.info(f"Recorded audit: server_id={server_id}, action={action}, actor={actor}")
    
    return {"status": "recorded", "timestamp": timestamp}


@app.get("/api/submissions/verdicts")
async def get_verdict_options():
    """Get available verdict options for filtering."""
    sql = """
    SELECT DISTINCT verdict
    FROM mcp_server_registry
    WHERE verdict IS NOT NULL
    ORDER BY verdict
    """
    rows = ws_query(sql)
    verdicts = [row["verdict"] for row in rows]
    return {"verdicts": verdicts}


@app.get("/api/submissions/sources")
async def get_source_options():
    """Get available registry source options for filtering."""
    sql = """
    SELECT DISTINCT registry_source
    FROM mcp_server_registry
    WHERE registry_source IS NOT NULL
    ORDER BY registry_source
    """
    rows = ws_query(sql)
    sources = [row["registry_source"] for row in rows]
    return {"sources": sources}


@app.get("/api/submissions/search")
async def search_submissions(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(20, ge=1, le=100),
):
    """Search submissions by name or description."""
    search_pattern = f"%{q}%"
    sql = """
    SELECT 
        server_id,
        name,
        url,
        description,
        trust_score,
        verdict,
        registry_source
    FROM mcp_server_registry
    WHERE name ILIKE %(pattern)s
       OR description ILIKE %(pattern)s
       OR server_id ILIKE %(pattern)s
    ORDER BY trust_score DESC NULLS LAST
    LIMIT %(limit)s
    """
    rows = ws_query(sql, {"pattern": search_pattern, "limit": limit})
    return {"query": q, "results": rows, "count": len(rows)}


@app.get("/api/submissions/{server_id}/history")
async def get_submission_history(
    server_id: str,
    days: int = Query(30, ge=1, le=365, description="Number of days of history"),
):
    """Get historical signal score changes for a submission."""
    sql = """
    SELECT 
        signal_name,
        score,
        scored_at
    FROM mcp_signal_scores
    WHERE server_id = %(server_id)s
      AND scored_at >= NOW() - INTERVAL '%(days)s days'
    ORDER BY scored_at DESC
    """
    rows = ws_query(sql, {"server_id": server_id, "days": days})
    
    history_by_signal = {}
    for row in rows:
        signal = row["signal_name"]
        if signal not in history_by_signal:
            history_by_signal[signal] = []
        history_by_signal[signal].append({
            "score": row["score"],
            "scored_at": row["scored_at"],
        })
    
    return {
        "server_id": server_id,
        "days": days,
        "history": history_by_signal,
    }


def run():
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    run()