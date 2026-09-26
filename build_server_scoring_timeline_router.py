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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/server_scoring_timeline_router.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = "server_scoring_timeline_router"
SERVICE_PORT = 8791
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"
PID_FILE = "/tmp/server_scoring_timeline_router.pid"

app = FastAPI(title="Server Scoring Timeline API", version="1.0.0")

_process_start_time: float = time.time()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str) -> dict:
    payload = {"sql": sql}
    resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table: str, rows: list) -> dict:
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql: str) -> dict:
    payload = {"sql": sql}
    resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat() -> None:
    ts = utc_now_iso()
    uptime = time.time() - _process_start_time
    meta = {"uptime_seconds": round(uptime, 1)}
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "status": "running",
            "ts": ts,
            "meta": str(meta)
        }])
    except Exception as e:
        logger.warning("Heartbeat failed: %s", e)


def check_single_instance() -> None:
    pid_file = PID_FILE
    if os.path.exists(pid_file):
        with open(pid_file, 'r') as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error("Another instance already running with PID %s. Exiting.", old_pid)
            sys.exit(1)
        except (OSError, ValueError):
            pass
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame) -> None:
    logger.info("Received signal %d, shutting down gracefully...", signum)
    remove_pid_file()
    sys.exit(0)


@app.on_event("startup")
async def startup_event():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    ensure_timeline_table()
    logger.info("Server scoring timeline router started on port %d", SERVICE_PORT)


def ensure_timeline_table() -> None:
    create_sql = """
    CREATE TABLE IF NOT EXISTS mcp_server_scoring_timeline (
        timeline_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        scored_at TIMESTAMPTZ NOT NULL,
        trust_score DECIMAL(5,2),
        verdict VARCHAR(50),
        risk_tier VARCHAR(30),
        signal_scores JSON,
        evidence_summary JSON,
        metadata JSON,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
    )
    """
    try:
        ws_execute(create_sql)
        logger.info("Ensured mcp_server_scoring_timeline table exists")
    except Exception as e:
        logger.warning("Table creation warning: %s", e)


@app.get("/health")
async def health():
    uptime = time.time() - _process_start_time
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime_seconds": round(uptime, 1)
    }


@app.get("/api/v1/timeline/{server_id}")
async def get_timeline(
    server_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0)
):
    try:
        sql = f"""
        SELECT 
            timeline_id,
            server_id,
            scored_at,
            trust_score,
            verdict,
            risk_tier,
            signal_scores,
            evidence_summary,
            metadata
        FROM mcp_server_scoring_timeline
        WHERE server_id = '{server_id}'
        ORDER BY scored_at DESC
        LIMIT {limit}
        OFFSET {offset}
        """
        result = ws_query(sql)
        rows = result.get("rows", [])
        count_result = ws_query(
            f"SELECT COUNT(*) as cnt FROM mcp_server_scoring_timeline WHERE server_id = '{server_id}'"
        )
        total = count_result.get("rows", [{}])[0].get("cnt", 0)
        return {
            "server_id": server_id,
            "timeline": rows,
            "total": total,
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        logger.error("Failed to get timeline for server %s: %s", server_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/timeline/{server_id}/latest")
async def get_latest_timeline_entry(server_id: str):
    try:
        sql = f"""
        SELECT 
            timeline_id,
            server_id,
            scored_at,
            trust_score,
            verdict,
            risk_tier,
            signal_scores,
            evidence_summary,
            metadata
        FROM mcp_server_scoring_timeline
        WHERE server_id = '{server_id}'
        ORDER BY scored_at DESC
        LIMIT 1
        """
        result = ws_query(sql)
        rows = result.get("rows", [])
        if not rows:
            raise HTTPException(status_code=404, detail=f"No timeline entries found for server {server_id}")
        return rows[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get latest timeline for server %s: %s", server_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/timeline/{server_id}/append")
async def append_timeline_entry(
    server_id: str,
    trust_score: Optional[float] = None,
    verdict: Optional[str] = None,
    risk_tier: Optional[str] = None,
    signal_scores: Optional[dict] = None,
    evidence_summary: Optional[dict] = None,
    metadata: Optional[dict] = None
):
    ts = utc_now_iso()
    import hashlib
    content = f"{server_id}:{ts}:{trust_score}"
    timeline_id = hashlib.sha256(content.encode()).hexdigest()[:32]
    
    row = {
        "timeline_id": timeline_id,
        "server_id": server_id,
        "scored_at": ts,
        "trust_score": trust_score,
        "verdict": verdict,
        "risk_tier": risk_tier,
        "signal_scores": signal_scores if signal_scores else {},
        "evidence_summary": evidence_summary if evidence_summary else {},
        "metadata": metadata if metadata else {},
        "created_at": ts
    }
    
    try:
        ws_write("mcp_server_scoring_timeline", [row])
        return {"status": "ok", "timeline_id": timeline_id, "server_id": server_id, "scored_at": ts}
    except Exception as e:
        logger.error("Failed to append timeline entry for server %s: %s", server_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/timeline/{server_id}/stats")
async def get_timeline_stats(server_id: str):
    try:
        sql = f"""
        SELECT 
            COUNT(*) as total_entries,
            MIN(scored_at) as first_entry,
            MAX(scored_at) as last_entry,
            AVG(trust_score) as avg_trust_score,
            MIN(trust_score) as min_trust_score,
            MAX(trust_score) as max_trust_score,
            COUNT(DISTINCT verdict) as distinct_verdicts,
            COUNT(DISTINCT risk_tier) as distinct_risk_tiers
        FROM mcp_server_scoring_timeline
        WHERE server_id = '{server_id}'
        """
        result = ws_query(sql)
        rows = result.get("rows", [])
        if not rows:
            raise HTTPException(status_code=404, detail=f"No timeline entries found for server {server_id}")
        return rows[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get timeline stats for server %s: %s", server_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/timeline/{server_id}/verdict-history")
async def get_verdict_history(server_id: str, limit: int = Query(default=100, ge=1, le=1000)):
    try:
        sql = f"""
        SELECT 
            scored_at,
            verdict,
            trust_score,
            risk_tier
        FROM mcp_server_scoring_timeline
        WHERE server_id = '{server_id}'
        ORDER BY scored_at DESC
        LIMIT {limit}
        """
        result = ws_query(sql)
        return {
            "server_id": server_id,
            "verdict_history": result.get("rows", [])
        }
    except Exception as e:
        logger.error("Failed to get verdict history for server %s: %s", server_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/timeline/{server_id}/score-history")
async def get_score_history(server_id: str, limit: int = Query(default=100, ge=1, le=1000)):
    try:
        sql = f"""
        SELECT 
            scored_at,
            trust_score
        FROM mcp_server_scoring_timeline
        WHERE server_id = '{server_id}'
          AND trust_score IS NOT NULL
        ORDER BY scored_at ASC
        LIMIT {limit}
        """
        result = ws_query(sql)
        return {
            "server_id": server_id,
            "score_history": result.get("rows", [])
        }
    except Exception as e:
        logger.error("Failed to get score history for server %s: %s", server_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/timeline/changes")
async def get_recent_timeline_changes(
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=100, ge=1, le=1000)
):
    try:
        sql = f"""
        SELECT 
            t1.server_id,
            t1.scored_at as changed_at,
            t1.verdict as new_verdict,
            t1.trust_score as new_score,
            t2.verdict as previous_verdict,
            t2.trust_score as previous_score
        FROM mcp_server_scoring_timeline t1
        LEFT JOIN mcp_server_scoring_timeline t2 ON t1.server_id = t2.server_id 
            AND t2.scored_at < t1.scored_at
        WHERE t1.scored_at >= NOW() - INTERVAL '{hours} hours'
          AND t2.scored_at IS NULL
        ORDER BY t1.scored_at DESC
        LIMIT {limit}
        """
        result = ws_query(sql)
        return {
            "hours": hours,
            "changes": result.get("rows", [])
        }
    except Exception as e:
        logger.error("Failed to get recent timeline changes: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/servers/top-changed")
async def get_top_changed_servers(limit: int = Query(default=20, ge=1, le=100)):
    try:
        sql = f"""
        WITH recent_changes AS (
            SELECT server_id, scored_at, verdict, trust_score,
                   LAG(verdict) OVER (PARTITION BY server_id ORDER BY scored_at) as prev_verdict,
                   LAG(trust_score) OVER (PARTITION BY server_id ORDER BY scored_at) as prev_score
            FROM mcp_server_scoring_timeline
            WHERE scored_at >= NOW() - INTERVAL '7 days'
        )
        SELECT 
            server_id,
            COUNT(*) as change_count,
            MAX(scored_at) as last_change,
            MAX(verdict) as current_verdict,
            MAX(trust_score) as current_score,
            COUNT(DISTINCT CASE WHEN verdict != prev_verdict THEN 1 END) as verdict_changes
        FROM recent_changes
        GROUP BY server_id
        HAVING COUNT(*) > 1
        ORDER BY change_count DESC
        LIMIT {limit}
        """
        result = ws_query(sql)
        return {
            "top_changed": result.get("rows", [])
        }
    except Exception as e:
        logger.error("Failed to get top changed servers: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


def run():
    send_heartbeat()
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT, log_level="info")


if __name__ == "__main__":
    run()