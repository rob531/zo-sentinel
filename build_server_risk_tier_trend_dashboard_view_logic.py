import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Service constants
SERVICE_NAME = "server_risk_tier_trend_dashboard"
SERVICE_PORT = 8796
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = WRITE_SERVICE_URL
EXECUTE_SERVICE_URL = WRITE_SERVICE_URL
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

# Logging setup
LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(str(LOG_FILE)), logging.StreamHandler()],
)
log = logging.getLogger(SERVICE_NAME)

# FastAPI app
app = FastAPI(title=f"{SERVICE_NAME} API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Process start time for uptime calculation
_process_start_time = time.time()


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """Query write_service for SELECT statements."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(
            f"{QUERY_SERVICE_URL}/query",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except requests.exceptions.RequestException as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to write_service."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"ws_write failed: {e}")
        return False


def send_heartbeat(status: str = "running", meta: Optional[Dict[str, Any]] = None) -> None:
    """Send heartbeat to service_health table."""
    rows = [
        {
            "service": SERVICE_NAME,
            "last_heartbeat": utc_now_iso(),
            "status": status,
            "meta": meta or {},
        }
    ]
    ws_write("service_health", rows)


def check_single_instance() -> bool:
    """Ensure only one instance runs."""
    pid = os.getpid()
    try:
        with open(PID_FILE) as f:
            existing_pid = int(f.read().strip())
        if existing_pid != pid:
            import os as os_module
            if os_module.name == "posix":
                import signal
                try:
                    os_module.kill(existing_pid, 0)
                    log.warning(f"Instance already running with PID {existing_pid}")
                    return False
                except OSError:
                    log.info(f"Stale PID file, taking over")
    except (FileNotFoundError, ValueError):
        pass
    with open(PID_FILE, "w") as f:
        f.write(str(pid))
    return True


def remove_pid_file() -> None:
    """Remove the PID file on shutdown."""
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame) -> None:
    """Handle shutdown signals gracefully."""
    log.info(f"Received signal {signum}, shutting down gracefully")
    send_heartbeat(status="stopping")
    remove_pid_file()
    sys.exit(0)


# ============================================================
# Risk Tier Trend API Endpoints
# ============================================================


@app.get("/health")
def health():
    """Health check endpoint."""
    uptime = time.time() - _process_start_time
    return {"status": "ok", "service": SERVICE_NAME, "uptime_seconds": round(uptime, 2)}


@app.get("/api/risk-tiers")
def get_risk_tier_distribution(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    """
    Get current distribution of servers across risk tiers.
    Returns counts per risk_tier from mcp_risk_register.
    """
    sql = """
        SELECT 
            risk_tier,
            COUNT(*) as server_count,
            AVG(risk_rank) as avg_risk_rank,
            MAX(computed_at) as last_computed
        FROM mcp_risk_register
        GROUP BY risk_tier
        ORDER BY server_count DESC
        LIMIT ? OFFSET ?
    """
    rows = ws_query(sql, [limit, offset])
    
    total_sql = "SELECT COUNT(DISTINCT risk_tier) as total FROM mcp_risk_register"
    total_rows = ws_query(total_sql)
    total_tiers = total_rows[0]["total"] if total_rows else 0
    
    return {
        "tiers": rows,
        "total_tiers": total_tiers,
        "limit": limit,
        "offset": offset,
        "ts": utc_now_iso(),
    }


@app.get("/api/risk-tiers/trend")
def get_risk_tier_trend(
    days: int = Query(default=30, ge=1, le=365),
    granularity: str = Query(default="day", regex="^(day|week)$"),
) -> Dict[str, Any]:
    """
    Get risk tier trends over time.
    Aggregates mcp_risk_register data to show how risk tiers evolved.
    """
    if granularity == "day":
        date_trunc = "DATE(computed_at)"
        num_buckets = days
    else:
        date_trunc = "DATE_TRUNC('week', computed_at)"
        num_buckets = days // 7 + 1

    sql = f"""
        WITH daily_tiers AS (
            SELECT 
                {date_trunc} as period,
                risk_tier,
                COUNT(*) as server_count,
                AVG(risk_rank) as avg_risk_rank
            FROM mcp_risk_register
            WHERE computed_at >= CURRENT_DATE - INTERVAL '{(days * 2)} days'
            GROUP BY period, risk_tier
            ORDER BY period DESC, server_count DESC
        ),
        pivot AS (
            SELECT 
                period,
                COALESCE(SUM(CASE WHEN risk_tier = 'CRITICAL' THEN server_count ELSE 0 END), 0) as critical,
                COALESCE(SUM(CASE WHEN risk_tier = 'HIGH' THEN server_count ELSE 0 END), 0) as high,
                COALESCE(SUM(CASE WHEN risk_tier = 'MEDIUM' THEN server_count ELSE 0 END), 0) as medium,
                COALESCE(SUM(CASE WHEN risk_tier = 'LOW' THEN server_count ELSE 0 END), 0) as low,
                COALESCE(SUM(CASE WHEN risk_tier = 'MINIMAL' THEN server_count ELSE 0 END), 0) as minimal,
                SUM(server_count) as total_servers,
                AVG(avg_risk_rank) as avg_risk_rank
            FROM daily_tiers
            GROUP BY period
        )
        SELECT * FROM pivot
        ORDER BY period DESC
        LIMIT ?
    """
    rows = ws_query(sql, [num_buckets])
    
    return {
        "trend": rows,
        "days": days,
        "granularity": granularity,
        "ts": utc_now_iso(),
    }


@app.get("/api/risk-tiers/top-threats")
def get_top_threat_servers(
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    """
    Get servers with highest threat counts.
    Returns top risk servers ordered by threat_count.
    """
    sql = """
        SELECT 
            r.server_id,
            r.risk_tier,
            r.risk_rank,
            r.threat_count,
            r.computed_at,
            COALESCE(s.name, 'Unknown') as server_name,
            COALESCE(s.verdict, 'UNKNOWN') as verdict,
            COALESCE(s.trust_score, 0) as trust_score
        FROM mcp_risk_register r
        LEFT JOIN mcp_server_registry s ON r.server_id = s.server_id
        WHERE r.threat_count > 0
        ORDER BY r.threat_count DESC, r.risk_rank DESC
        LIMIT ?
    """
    rows = ws_query(sql, [limit])
    
    return {
        "servers": rows,
        "count": len(rows),
        "ts": utc_now_iso(),
    }


@app.get("/api/risk-tiers/movement")
def get_risk_tier_movement(
    days: int = Query(default=7, ge=1, le=30),
) -> Dict[str, Any]:
    """
    Get servers that changed risk tiers within the period.
    Shows escalation and de-escalation patterns.
    """
    sql = """
        WITH latest AS (
            SELECT server_id, risk_tier, risk_rank, computed_at,
                   ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY computed_at DESC) as rn
            FROM mcp_risk_register
        ),
        previous AS (
            SELECT server_id, risk_tier as prev_tier, risk_rank as prev_rank, computed_at,
                   ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY computed_at DESC) as rn
            FROM mcp_risk_register
            WHERE computed_at < CURRENT_DATE - INTERVAL '1 day'
        ),
        movement AS (
            SELECT 
                l.server_id,
                p.prev_tier,
                l.risk_tier as current_tier,
                p.prev_rank,
                l.risk_rank as current_rank,
                CASE 
                    WHEN l.risk_rank > p.prev_rank THEN 'ESCALATED'
                    WHEN l.risk_rank < p.prev_rank THEN 'DE_ESCALATED'
                    ELSE 'STABLE'
                END as movement_type,
                ABS(l.risk_rank - p.prev_rank) as rank_change
            FROM latest l
            JOIN previous p ON l.server_id = p.server_id
            WHERE l.rn = 1 AND p.rn = 1
              AND l.risk_tier != p.prev_tier
        )
        SELECT 
            movement_type,
            prev_tier,
            current_tier,
            COUNT(*) as server_count
        FROM movement
        GROUP BY movement_type, prev_tier, current_tier
        ORDER BY server_count DESC
    """
    rows = ws_query(sql)
    
    total_sql = """
        WITH latest AS (
            SELECT server_id, risk_tier, risk_rank,
                   ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY computed_at DESC) as rn
            FROM mcp_risk_register
        ),
        previous AS (
            SELECT server_id, risk_tier as prev_tier, risk_rank as prev_rank,
                   ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY computed_at DESC) as rn
            FROM mcp_risk_register
            WHERE computed_at < CURRENT_DATE - INTERVAL '1 day'
        )
        SELECT COUNT(*) as total_changed
        FROM latest l
        JOIN previous p ON l.server_id = p.server_id
        WHERE l.rn = 1 AND p.rn = 1 AND l.risk_tier != p.prev_tier
    """
    total_rows = ws_query(total_sql)
    total_changed = total_rows[0]["total_changed"] if total_rows else 0
    
    return {
        "movement": rows,
        "total_changed": total_changed,
        "period_days": days,
        "ts": utc_now_iso(),
    }


@app.get("/api/risk-tiers/summary")
def get_risk_summary() -> Dict[str, Any]:
    """
    Get a high-level summary of risk tier distribution.
    Quick snapshot for dashboard overview.
    """
    sql = """
        WITH latest AS (
            SELECT server_id, risk_tier, risk_rank, threat_count, computed_at,
                   ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY computed_at DESC) as rn
            FROM mcp_risk_register
        )
        SELECT 
            risk_tier,
            COUNT(*) as server_count,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) as percentage,
            AVG(risk_rank) as avg_risk_rank,
            SUM(threat_count) as total_threats,
            MAX(computed_at) as last_updated
        FROM latest
        WHERE rn = 1
        GROUP BY risk_tier
        ORDER BY 
            CASE risk_tier 
                WHEN 'CRITICAL' THEN 1 
                WHEN 'HIGH' THEN 2 
                WHEN 'MEDIUM' THEN 3 
                WHEN 'LOW' THEN 4 
                WHEN 'MINIMAL' THEN 5 
                ELSE 6 
            END
    """
    rows = ws_query(sql)
    
    total_sql = "SELECT COUNT(DISTINCT server_id) as total FROM mcp_risk_register"
    total_rows = ws_query(total_sql)
    total_servers = total_rows[0]["total"] if total_rows else 0
    
    return {
        "summary": rows,
        "total_servers": total_servers,
        "ts": utc_now_iso(),
    }


@app.get("/api/risk-tiers/{server_id}")
def get_server_risk_detail(server_id: str) -> Dict[str, Any]:
    """
    Get detailed risk information for a specific server.
    Includes current tier, history, and related signals.
    """
    risk_sql = """
        WITH latest AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY computed_at DESC) as rn
            FROM mcp_risk_register
            WHERE server_id = ?
        )
        SELECT risk_tier, risk_rank, threat_count, computed_at
        FROM latest WHERE rn = 1
    """
    risk_rows = ws_query(risk_sql, [server_id])
    
    if not risk_rows:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found in risk register")
    
    current_risk = risk_rows[0]
    
    history_sql = """
        SELECT risk_tier, risk_rank, threat_count, computed_at
        FROM mcp_risk_register
        WHERE server_id = ?
        ORDER BY computed_at DESC
        LIMIT 30
    """
    history = ws_query(history_sql, [server_id])
    
    signals_sql = """
        SELECT signal_name, score, evidence, computed_at
        FROM mcp_signal_scores
        WHERE server_id = ?
        ORDER BY computed_at DESC
        LIMIT 20
    """
    signals = ws_query(signals_sql, [server_id])
    
    registry_sql = """
        SELECT name, url, description, verdict, trust_score, registry_source
        FROM mcp_server_registry
        WHERE server_id = ?
    """
    registry_rows = ws_query(registry_sql, [server_id])
    registry = registry_rows[0] if registry_rows else {}
    
    return {
        "server_id": server_id,
        "current": current_risk,
        "history": history,
        "signals": signals,
        "registry": registry,
        "ts": utc_now_iso(),
    }


# ============================================================
# Daemon lifecycle
# ============================================================


def run():
    """Start the FastAPI server as a daemon."""
    import signal
    
    if not check_single_instance():
        log.error("Another instance is running. Exiting.")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    
    send_heartbeat(status="starting")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=SERVICE_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    run()