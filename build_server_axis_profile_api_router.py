import os
import logging
import signal
import sys
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, Query
from typing import Optional
import time

LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "server_axis_profile_api_router.log"
PID_FILE = "/tmp/server_axis_profile_api_router.pid"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("server_axis_profile_api_router")

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"
EXECUTE_SERVICE_URL = "http://localhost:8772"
SERVICE_NAME = "server_axis_profile_api_router"
SERVICE_PORT = 8786

app = FastAPI(title="Server Axis Profile API Router")


def ws_write(table: str, rows: list) -> dict:
    resp = requests.post(
        WRITE_SERVICE_URL + "/write",
        json={"table": table, "rows": rows},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str) -> list:
    resp = requests.post(
        QUERY_SERVICE_URL + "/query",
        json={"sql": sql},
        timeout=30
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_execute(sql: str) -> dict:
    resp = requests.post(
        EXECUTE_SERVICE_URL + "/execute",
        json={"sql": sql},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def check_single_instance():
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            logger.error("Another instance running with PID %s", old_pid)
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    logger.info("Received signal %d, shutting down gracefully", signum)
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": utc_now_iso(),
            "status": "running",
            "meta": "{}"
        }])
    except Exception as e:
        logger.warning("Failed to send heartbeat: %s", e)


def verify_api_key(authorization: Optional[str] = Header(None)) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    token = authorization[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Empty token")
    return token


def get_server_profile(server_id: str) -> dict:
    rows = ws_query(f"""
        SELECT 
            server_id,
            name,
            description,
            url,
            trust_score,
            verdict,
            registry_source,
            scan_count,
            risk_tier,
            risk_rank,
            threat_count,
            computed_at
        FROM mcp_server_registry
        WHERE server_id = '{server_id}'
    """)
    if not rows:
        return None
    
    server = rows[0]
    
    signal_rows = ws_query(f"""
        SELECT 
            signal_name,
            score,
            evidence,
            scored_at
        FROM mcp_signal_scores
        WHERE server_id = '{server_id}'
        ORDER BY signal_name
    """)
    
    server["signals"] = signal_rows
    
    threat_rows = ws_query(f"""
        SELECT 
            threat_type,
            severity,
            evidence,
            reported_at
        FROM mcp_threat_associations
        WHERE server_id = '{server_id}'
        ORDER BY reported_at DESC
    """)
    
    server["threat_associations"] = threat_rows
    
    return server


def list_server_profiles(limit: int = 100, offset: int = 0, verdict: Optional[str] = None, risk_tier: Optional[str] = None) -> dict:
    where_clauses = []
    if verdict:
        where_clauses.append(f"verdict = '{verdict}'")
    if risk_tier:
        where_clauses.append(f"risk_tier = '{risk_tier}'")
    
    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
    
    count_rows = ws_query(f"SELECT COUNT(*) as total FROM mcp_server_registry WHERE {where_sql}")
    total = count_rows[0]["total"] if count_rows else 0
    
    servers = ws_query(f"""
        SELECT 
            server_id,
            name,
            description,
            url,
            trust_score,
            verdict,
            registry_source,
            scan_count,
            risk_tier,
            risk_rank,
            computed_at
        FROM mcp_server_registry
        WHERE {where_sql}
        ORDER BY trust_score DESC NULLS LAST, scan_count DESC
        LIMIT {limit} OFFSET {offset}
    """)
    
    return {
        "servers": servers,
        "total": total,
        "limit": limit,
        "offset": offset
    }


def get_server_axis_summary() -> dict:
    verdict_dist = ws_query("""
        SELECT verdict, COUNT(*) as count 
        FROM mcp_server_registry 
        GROUP BY verdict
    """)
    
    risk_dist = ws_query("""
        SELECT risk_tier, COUNT(*) as count 
        FROM mcp_risk_register 
        GROUP BY risk_tier
    """)
    
    trust_stats = ws_query("""
        SELECT 
            AVG(trust_score) as avg_trust,
            MIN(trust_score) as min_trust,
            MAX(trust_score) as max_trust,
            COUNT(*) as total
        FROM mcp_server_registry
        WHERE trust_score IS NOT NULL
    """)
    
    signal_coverage = ws_query("""
        SELECT 
            signal_name,
            COUNT(*) as count
        FROM mcp_signal_scores
        GROUP BY signal_name
    """)
    
    return {
        "verdict_distribution": verdict_dist,
        "risk_tier_distribution": risk_dist,
        "trust_score_stats": trust_stats[0] if trust_stats else {},
        "signal_coverage": signal_coverage,
        "computed_at": utc_now_iso()
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME, "timestamp": utc_now_iso()}


@app.get("/api/v1/servers/profile/{server_id}")
def get_profile(server_id: str, authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    profile = get_server_profile(server_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    return profile


@app.get("/api/v1/servers/profile")
def list_profiles(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    verdict: Optional[str] = None,
    risk_tier: Optional[str] = None,
    authorization: Optional[str] = Header(None)
):
    verify_api_key(authorization)
    return list_server_profiles(limit=limit, offset=offset, verdict=verdict, risk_tier=risk_tier)


@app.get("/api/v1/servers/axis/summary")
def axis_summary(authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    return get_server_axis_summary()


@app.post("/api/v1/servers/profile/{server_id}/refresh")
def refresh_profile(server_id: str, authorization: Optional[str] = Header(None)):
    verify_api_key(authorization)
    ws_execute(f"UPDATE mcp_server_registry SET scan_count = scan_count + 1 WHERE server_id = '{server_id}'")
    return {"status": "ok", "server_id": server_id, "refreshed_at": utc_now_iso()}


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    logger.info("Starting %s on port %d", SERVICE_NAME, SERVICE_PORT)
    
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT, log_level="info")


if __name__ == "__main__":
    run()