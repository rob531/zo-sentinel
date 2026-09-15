import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel, Field, field_validator

# Constants
SERVICE_NAME = 'registry_quick_search_contract'
PORT = 8786
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = WRITE_SERVICE_URL + '/query'
WRITE_URL = WRITE_SERVICE_URL + '/write'
EXECUTE_URL = WRITE_SERVICE_URL + '/execute'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
LOG_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'
START_TIME = datetime.now(timezone.utc)

# FastAPI app
app = FastAPI(title=SERVICE_NAME, version='1.0.0')

# Logging
LOG_DIR = Path(LOG_FILE).parent
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(SERVICE_NAME)


def ws_query(sql: str) -> dict:
    """Query write_service."""
    resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table: str, rows: list) -> dict:
    """Write to write_service."""
    resp = requests.post(WRITE_URL, json={'table': table, 'rows': rows}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql: str) -> dict:
    """Execute DDL/DML via write_service."""
    resp = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Pydantic models
class SearchResponse(BaseModel):
    servers: list
    total: int
    query: str
    elapsed_ms: float


class RegistrySummaryResponse(BaseModel):
    total_servers: int
    verdict_distribution: dict
    avg_trust_score: float
    timestamp: str


class ServerDetailResponse(BaseModel):
    server: Optional[dict] = None
    found: bool = False


# Valid verdicts
VALID_VERDICTS = ['TRUSTED', 'AMBER', 'UNTRUSTED', 'UNKNOWN', 'KNOWN_THREAT', 'HIGH_RISK_ISOLATED', 'CAUTION_LIMITED', 'AMBER_UNVERIFIED', 'TRUSTED_RESEARCH', 'ENTERPRISE_CONTROLLED']


def check_single_instance():
    """Ensure only one instance runs."""
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        old_pid = int(pid_path.read_text().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance is running (PID {old_pid}). Exiting.")
            sys.exit(1)
        except OSError:
            log.warning(f"Stale PID file found (PID {old_pid}). Removing.")
            pid_path.unlink()
    pid_path.write_text(str(os.getpid()))
    log.info(f"Started with PID {os.getpid()}")


def remove_pid_file():
    """Remove PID file on exit."""
    try:
        Path(PID_FILE).unlink(missing_ok=True)
        log.info("PID file removed.")
    except Exception as e:
        log.error(f"Failed to remove PID file: {e}")


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    sig_name = signal.Signals(signum).name
    log.info(f"Received {sig_name}. Shutting down gracefully.")
    remove_pid_file()
    sys.exit(0)


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


@app.get('/health')
async def health():
    """Health check endpoint."""
    uptime_seconds = (datetime.now(timezone.utc) - START_TIME.replace(tzinfo=None)).total_seconds()
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        'uptime_seconds': round(uptime_seconds, 1)
    }


@app.get('/search', response_model=SearchResponse)
async def search(
    q: str = Query('', description='Search query (matches name and description)'),
    verdict: Optional[str] = Query(None, description='Filter by verdict'),
    trust_min: Optional[float] = Query(None, description='Minimum trust score (0-100)'),
    trust_max: Optional[float] = Query(None, description='Maximum trust score (0-100)'),
    registry_source: Optional[str] = Query(None, description='Filter by registry source'),
    order_by: str = Query('score', description='Order by: score or name'),
    order_dir: str = Query('desc', description='Order direction: asc or desc'),
    limit: int = Query(20, ge=1, le=100, description='Results per page'),
    offset: int = Query(0, ge=0, description='Offset for pagination')
):
    """
    Quick search across MCP server registry.
    Supports full-text search on name and description,
    filtering by verdict and trust score range,
    and paginated results.
    """
    start = time.time()
    
    # Validate inputs
    if verdict and verdict.upper() not in VALID_VERDICTS:
        raise HTTPException(status_code=400, detail=f'Invalid verdict. Must be one of: {VALID_VERDICTS}')
    if trust_min is not None and (trust_min < 0 or trust_min > 100):
        raise HTTPException(status_code=400, detail='trust_min must be between 0 and 100')
    if trust_max is not None and (trust_max < 0 or trust_max > 100):
        raise HTTPException(status_code=400, detail='trust_max must be between 0 and 100')
    if trust_min is not None and trust_max is not None and trust_min > trust_max:
        raise HTTPException(status_code=400, detail='trust_min cannot be greater than trust_max')
    if order_by not in ['score', 'name']:
        raise HTTPException(status_code=400, detail='order_by must be score or name')
    if order_dir not in ['asc', 'desc']:
        raise HTTPException(status_code=400, detail='order_dir must be asc or desc')
    
    # Build filter conditions
    conditions = []
    params = []
    
    if q:
        conditions.append("(LOWER(name) LIKE LOWER(?) OR LOWER(description) LIKE LOWER(?))")
        params.extend([f'%{q}%', f'%{q}%'])
    
    if verdict:
        conditions.append("verdict = ?")
        params.append(verdict.upper())
    
    if trust_min is not None:
        conditions.append("trust_score >= ?")
        params.append(trust_min)
    
    if trust_max is not None:
        conditions.append("trust_score <= ?")
        params.append(trust_max)
    
    if registry_source:
        conditions.append("registry_source = ?")
        params.append(registry_source)
    
    where_clause = ' AND '.join(conditions) if conditions else '1=1'
    
    # Build ORDER BY
    order_col = 'trust_score' if order_by == 'score' else 'name'
    order_direction = 'DESC' if order_dir == 'desc' else 'ASC'
    
    # Count total
    count_sql = f"SELECT COUNT(*) as cnt FROM mcp_server_registry WHERE {where_clause}"
    count_result = ws_query(count_sql)
    total = count_result.get('rows', [{}])[0].get('cnt', 0) if count_result.get('rows') else 0
    
    # Fetch servers
    query_sql = f"""
    SELECT 
        server_id, name, url, description, trust_score, verdict,
        registry_source, scan_count, first_seen, last_seen
    FROM mcp_server_registry
    WHERE {where_clause}
    ORDER BY {order_col} {order_direction}
    LIMIT {limit} OFFSET {offset}
    """
    
    try:
        result = ws_query(query_sql)
        servers = result.get('rows', [])
    except Exception as e:
        log.error(f"Search query failed: {e}")
        servers = []
    
    elapsed_ms = round((time.time() - start) * 1000, 2)
    
    return SearchResponse(
        servers=servers,
        total=total,
        query=q,
        elapsed_ms=elapsed_ms
    )


@app.get('/summary', response_model=RegistrySummaryResponse)
async def summary():
    """Get registry summary statistics."""
    try:
        total_sql = "SELECT COUNT(*) as cnt, AVG(trust_score) as avg_score FROM mcp_server_registry"
        total_result = ws_query(total_sql)
        total_row = total_result.get('rows', [{}])[0] if total_result.get('rows') else {}
        
        verdict_sql = """
        SELECT verdict, COUNT(*) as cnt 
        FROM mcp_server_registry 
        WHERE verdict IS NOT NULL 
        GROUP BY verdict
        """
        verdict_result = ws_query(verdict_sql)
        verdict_dist = {row.get('verdict', 'UNKNOWN'): row.get('cnt', 0) for row in verdict_result.get('rows', [])}
        
        return RegistrySummaryResponse(
            total_servers=total_row.get('cnt', 0),
            verdict_distribution=verdict_dist,
            avg_trust_score=round(total_row.get('avg_score', 0) or 0, 2),
            timestamp=utc_now_iso()
        )
    except Exception as e:
        log.error(f"Summary query failed: {e}")
        return RegistrySummaryResponse(
            total_servers=0,
            verdict_distribution={},
            avg_trust_score=0.0,
            timestamp=utc_now_iso()
        )


@app.get('/detail/{server_id}', response_model=ServerDetailResponse)
async def detail(server_id: str):
    """Get detailed info for a specific server."""
    if not server_id or len(server_id) < 8:
        raise HTTPException(status_code=400, detail='Invalid server_id format')
    
    sql = f"""
    SELECT 
        server_id, name, url, description, trust_score, verdict,
        registry_source, scan_count, first_seen, last_seen, last_scanned,
        last_assessed
    FROM mcp_server_registry
    WHERE server_id = ?
    """
    
    try:
        result = ws_query(sql)
        rows = result.get('rows', [])
        if rows:
            return ServerDetailResponse(server=rows[0], found=True)
        return ServerDetailResponse(server=None, found=False)
    except Exception as e:
        log.error(f"Detail query failed for {server_id}: {e}")
        raise HTTPException(status_code=500, detail='Failed to fetch server detail')


@app.get('/verdicts')
async def verdicts():
    """List valid verdict values."""
    return {'verdicts': VALID_VERDICTS}


def run():
    """Start the FastAPI service."""
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    check_single_instance()
    
    import uvicorn
    uvicorn.run(
        app,
        host='0.0.0.0',
        port=PORT,
        log_level='info',
        access_log=False
    )


if __name__ == '__main__':
    run()