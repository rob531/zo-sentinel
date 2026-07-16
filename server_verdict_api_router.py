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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/server_verdict_api_router.log')]
)
log = logging.getLogger(__name__)

SERVICE_NAME = 'server_verdict_api_router'
PORT = 8786
PID_FILE = '/tmp/server_verdict_api_router.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
WRITE_URL = 'http://localhost:8772/write'
EXECUTE_URL = 'http://localhost:8772/execute'
POLL_SECS = 30
HEARTBEAT_INTERVAL = 60

app = FastAPI(title='Server Verdict API Router', version='1.0.0')


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    payload: Dict[str, Any] = {'sql': sql}
    if params:
        payload['params'] = params
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except requests.exceptions.RequestException as e:
        log.error(f'ws_query failed: {e}')
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    payload = {'table': table, 'rows': rows}
    try:
        resp = requests.post(WRITE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error(f'ws_write failed: {e}')
        return False


def ws_execute(sql: str, params: Optional[List[Any]] = None) -> bool:
    payload: Dict[str, Any] = {'sql': sql}
    if params:
        payload['params'] = params
    try:
        resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error(f'ws_execute failed: {e}')
        return False


def send_heartbeat(status: str = 'running', meta: Optional[Dict[str, Any]] = None) -> None:
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': status,
        'meta': meta or {}
    }
    ws_write('service_health', [row])


def check_single_instance() -> None:
    try:
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        os.kill(old_pid, 0)
        log.error(f'Instance already running with PID {old_pid}')
        sys.exit(1)
    except (FileNotFoundError, ProcessLookupError, ValueError):
        pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass


def signal_handler(signum: int, frame: Any) -> None:
    log.info(f'Received signal {signum}, shutting down gracefully')
    remove_pid_file()
    sys.exit(0)


@app.on_event('startup')
async def startup_event() -> None:
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info(f'{SERVICE_NAME} starting on port {PORT}')
    ws_execute('''
        CREATE TABLE IF NOT EXISTS service_health (
            service VARCHAR PRIMARY KEY,
            last_heartbeat TIMESTAMP,
            status VARCHAR,
            meta VARCHAR
        )
    ''')
    send_heartbeat('starting', {'version': '1.0.0', 'port': PORT})


@app.on_event('shutdown')
async def shutdown_event() -> None:
    log.info(f'{SERVICE_NAME} shutting down')
    send_heartbeat('stopped', {})
    remove_pid_file()


@app.get('/health')
async def health() -> Dict[str, Any]:
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        'timestamp': utc_now_iso()
    }


@app.get('/verdict/{server_id}')
async def get_server_verdict(server_id: str) -> Dict[str, Any]:
    sql = '''
        SELECT
            r.server_id,
            r.name,
            r.trust_score,
            r.verdict,
            r.registry_source,
            r.last_assessed,
            r.description
        FROM mcp_server_registry r
        WHERE r.server_id = ?
    '''
    rows = ws_query(sql, [server_id])
    if not rows:
        raise HTTPException(status_code=404, detail=f'Server {server_id} not found')
    row = rows[0]
    signal_sql = '''
        SELECT
            signal_name,
            score,
            evidence,
            scored_at
        FROM mcp_signal_scores
        WHERE server_id = ?
        ORDER BY signal_name
    '''
    signals = ws_query(signal_sql, [server_id])
    risk_sql = '''
        SELECT
            risk_tier,
            risk_rank,
            threat_count,
            computed_at
        FROM mcp_risk_register
        WHERE server_id = ?
    '''
    risk_rows = ws_query(risk_sql, [server_id])
    return {
        'server_id': row['server_id'],
        'name': row['name'],
        'trust_score': row['trust_score'],
        'verdict': row['verdict'],
        'registry_source': row['registry_source'],
        'last_assessed': row['last_assessed'],
        'description': row.get('description', ''),
        'signals': signals,
        'risk': risk_rows[0] if risk_rows else None
    }


@app.get('/verdicts/bulk')
async def get_bulk_verdicts(
    server_ids: str = Query(..., description='Comma-separated list of server IDs')
) -> Dict[str, Any]:
    id_list = [s.strip() for s in server_ids.split(',') if s.strip()]
    if not id_list:
        raise HTTPException(status_code=400, detail='No server IDs provided')
    if len(id_list) > 100:
        raise HTTPException(status_code=400, detail='Maximum 100 server IDs per request')
    placeholders = ','.join(['?' for _ in id_list])
    sql = f'''
        SELECT
            server_id,
            name,
            trust_score,
            verdict,
            registry_source,
            last_assessed
        FROM mcp_server_registry
        WHERE server_id IN ({placeholders})
    '''
    rows = ws_query(sql, id_list)
    return {
        'count': len(rows),
        'servers': rows
    }


@app.get('/verdicts/summary')
async def get_verdict_summary() -> Dict[str, Any]:
    sql = '''
        SELECT
            verdict,
            COUNT(*) as count,
            AVG(trust_score) as avg_trust_score
        FROM mcp_server_registry
        GROUP BY verdict
        ORDER BY count DESC
    '''
    verdict_counts = ws_query(sql)
    total_sql = 'SELECT COUNT(*) as total FROM mcp_server_registry'
    total_rows = ws_query(total_sql)
    total = total_rows[0]['total'] if total_rows else 0
    risk_sql = '''
        SELECT
            risk_tier,
            COUNT(*) as count
        FROM mcp_risk_register
        GROUP BY risk_tier
        ORDER BY count DESC
    '''
    risk_counts = ws_query(risk_sql)
    return {
        'total_servers': total,
        'by_verdict': verdict_counts,
        'by_risk_tier': risk_counts,
        'timestamp': utc_now_iso()
    }


@app.get('/verdicts/search')
async def search_verdicts(
    verdict: Optional[str] = None,
    risk_tier: Optional[str] = None,
    min_trust_score: Optional[float] = None,
    max_trust_score: Optional[float] = None,
    registry_source: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0)
) -> Dict[str, Any]:
    conditions = []
    params: List[Any] = []
    if verdict:
        conditions.append('verdict = ?')
        params.append(verdict)
    if risk_tier:
        conditions.append('r.server_id IN (SELECT server_id FROM mcp_risk_register WHERE risk_tier = ?)')
        params.append(risk_tier)
    if min_trust_score is not None:
        conditions.append('trust_score >= ?')
        params.append(min_trust_score)
    if max_trust_score is not None:
        conditions.append('trust_score <= ?')
        params.append(max_trust_score)
    if registry_source:
        conditions.append('registry_source = ?')
        params.append(registry_source)
    where_clause = ' AND '.join(conditions) if conditions else '1=1'
    count_sql = f'''
        SELECT COUNT(*) as total
        FROM mcp_server_registry r
        WHERE {where_clause}
    '''
    count_rows = ws_query(count_sql, params)
    total = count_rows[0]['total'] if count_rows else 0
    sql = f'''
        SELECT
            r.server_id,
            r.name,
            r.trust_score,
            r.verdict,
            r.registry_source,
            r.last_assessed,
            COALESCE(rr.risk_tier, 'UNKNOWN') as risk_tier
        FROM mcp_server_registry r
        LEFT JOIN mcp_risk_register rr ON r.server_id = rr.server_id
        WHERE {where_clause}
        ORDER BY r.trust_score DESC
        LIMIT ? OFFSET ?
    '''
    params.extend([limit, offset])
    rows = ws_query(sql, params)
    return {
        'total': total,
        'limit': limit,
        'offset': offset,
        'servers': rows
    }


@app.get('/verdicts/top-risks')
async def get_top_risks(limit: int = Query(20, ge=1, le=100)) -> Dict[str, Any]:
    sql = '''
        SELECT
            r.server_id,
            r.name,
            r.trust_score,
            r.verdict,
            rr.risk_tier,
            rr.risk_rank,
            rr.threat_count,
            rr.computed_at
        FROM mcp_server_registry r
        INNER JOIN mcp_risk_register rr ON r.server_id = rr.server_id
        WHERE rr.risk_tier IN ('CRITICAL', 'HIGH', 'MEDIUM')
        ORDER BY
            CASE rr.risk_tier
                WHEN 'CRITICAL' THEN 1
                WHEN 'HIGH' THEN 2
                WHEN 'MEDIUM' THEN 3
            END,
            rr.risk_rank ASC
        LIMIT ?
    '''
    rows = ws_query(sql, [limit])
    return {
        'count': len(rows),
        'high_risk_servers': rows
    }


@app.get('/verdicts/by-source/{registry_source}')
async def get_by_source(
    registry_source: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0)
) -> Dict[str, Any]:
    sql = '''
        SELECT
            server_id,
            name,
            trust_score,
            verdict,
            last_assessed
        FROM mcp_server_registry
        WHERE registry_source = ?
        ORDER BY trust_score DESC
        LIMIT ? OFFSET ?
    '''
    count_sql = 'SELECT COUNT(*) as total FROM mcp_server_registry WHERE registry_source = ?'
    count_rows = ws_query(count_sql, [registry_source])
    total = count_rows[0]['total'] if count_rows else 0
    rows = ws_query(sql, [registry_source, limit, offset])
    return {
        'registry_source': registry_source,
        'total': total,
        'limit': limit,
        'offset': offset,
        'servers': rows
    }


@app.get('/verdict/{server_id}/history')
async def get_verdict_history(
    server_id: str,
    limit: int = Query(50, ge=1, le=200)
) -> Dict[str, Any]:
    exists_sql = 'SELECT 1 FROM mcp_server_registry WHERE server_id = ?'
    if not ws_query(exists_sql, [server_id]):
        raise HTTPException(status_code=404, detail=f'Server {server_id} not found')
    sql = '''
        SELECT
            signal_name,
            score,
            evidence,
            scored_at
        FROM mcp_signal_scores
        WHERE server_id = ?
        ORDER BY scored_at DESC
        LIMIT ?
    '''
    rows = ws_query(sql, [server_id, limit])
    return {
        'server_id': server_id,
        'history': rows
    }


@app.get('/verdicts/known-threats')
async def get_known_threats() -> Dict[str, Any]:
    sql = '''
        SELECT
            r.server_id,
            r.name,
            r.trust_score,
            r.verdict,
            t.threat_type,
            t.severity,
            t.evidence,
            t.reported_at
        FROM mcp_server_registry r
        INNER JOIN mcp_threat_associations t ON r.server_id = t.server_id
        ORDER BY t.reported_at DESC
    '''
    rows = ws_query(sql)
    return {
        'count': len(rows),
        'threats': rows
    }


@app.get('/verdicts/trending')
async def get_trending_verdicts(
    hours: int = Query(24, ge=1, le=168)
) -> Dict[str, Any]:
    sql = '''
        SELECT
            r.server_id,
            r.name,
            r.trust_score,
            r.verdict,
            r.last_assessed
        FROM mcp_server_registry r
        WHERE r.last_assessed >= datetime('now', '-' || ? || ' hours')
        ORDER BY r.last_assessed DESC
        LIMIT 50
    '''
    rows = ws_query(sql, [hours])
    return {
        'hours': hours,
        'count': len(rows),
        'recently_assessed': rows
    }


def run() -> None:
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info(f'Starting {SERVICE_NAME} on port {PORT}')
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='info')


if __name__ == '__main__':
    run()