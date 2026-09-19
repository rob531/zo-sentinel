import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

LOG_DIR = Path('/home/workspace/logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / 'server_registry_source_distribution_dashboard_view_router.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('server_registry_source_distribution_dashboard_view_router')

SERVICE_NAME = 'server_registry_source_distribution_dashboard_view_router'
PORT = 8791
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
HEARTBEAT_INTERVAL = 60

app = FastAPI(title=SERVICE_NAME)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, timeout: float = 30.0) -> dict:
    try:
        resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f'ws_query failed: {e}')
        return {'rows': [], 'error': str(e)}


def ws_write(table: str, rows: list, timeout: float = 30.0) -> dict:
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={'table': table, 'rows': rows, 'wait': True}, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f'ws_write failed: {e}')
        return {'error': str(e)}


def check_single_instance() -> bool:
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            if os.path.exists(f'/proc/{old_pid}'):
                logger.warning(f'Instance already running with PID {old_pid}')
                return False
            else:
                pid_path.unlink()
        except (ValueError, IOError):
            pid_path.unlink()
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def signal_handler(signum, frame):
    logger.info(f'Received signal {signum}, shutting down gracefully')
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status: str = 'running', meta: dict = None):
    heartbeat_row = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': status,
        'meta': meta or {}
    }
    ws_write('service_health', [heartbeat_row])


def get_registry_source_distribution() -> dict:
    sql = '''
        SELECT 
            registry_source,
            COUNT(*) as server_count,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) as percentage,
            SUM(CASE WHEN verdict = 'TRUSTED' THEN 1 ELSE 0 END) as trusted_count,
            SUM(CASE WHEN verdict = 'AMBER' THEN 1 ELSE 0 END) as amber_count,
            SUM(CASE WHEN verdict = 'UNTRUSTED' THEN 1 ELSE 0 END) as untrusted_count,
            SUM(CASE WHEN verdict = 'UNKNOWN' THEN 1 ELSE 0 END) as unknown_count,
            SUM(CASE WHEN verdict = 'KNOWN_THREAT' THEN 1 ELSE 0 END) as known_threat_count,
            AVG(trust_score) as avg_trust_score,
            MIN(trust_score) as min_trust_score,
            MAX(trust_score) as max_trust_score
        FROM mcp_server_registry
        GROUP BY registry_source
        ORDER BY server_count DESC
    '''
    result = ws_query(sql)
    return result.get('rows', [])


def get_registry_source_by_verdict() -> dict:
    sql = '''
        SELECT 
            registry_source,
            verdict,
            COUNT(*) as count
        FROM mcp_server_registry
        GROUP BY registry_source, verdict
        ORDER BY registry_source, verdict
    '''
    result = ws_query(sql)
    return result.get('rows', [])


def get_registry_source_by_risk_tier() -> dict:
    sql = '''
        SELECT 
            r.registry_source,
            COALESCE(rr.risk_tier, 'UNASSESSED') as risk_tier,
            COUNT(*) as count
        FROM mcp_server_registry r
        LEFT JOIN mcp_risk_register rr ON r.server_id = rr.server_id
        GROUP BY r.registry_source, rr.risk_tier
        ORDER BY r.registry_source, risk_tier
    '''
    result = ws_query(sql)
    return result.get('rows', [])


def get_top_sources_by_trust_score() -> dict:
    sql = '''
        SELECT 
            registry_source,
            COUNT(*) as total_servers,
            AVG(trust_score) as avg_trust_score,
            SUM(CASE WHEN trust_score >= 80 THEN 1 ELSE 0 END) as high_trust_count,
            SUM(CASE WHEN trust_score >= 50 AND trust_score < 80 THEN 1 ELSE 0 END) as medium_trust_count,
            SUM(CASE WHEN trust_score < 50 THEN 1 ELSE 0 END) as low_trust_count
        FROM mcp_server_registry
        GROUP BY registry_source
        HAVING COUNT(*) >= 10
        ORDER BY avg_trust_score DESC
        LIMIT 20
    '''
    result = ws_query(sql)
    return result.get('rows', [])


@app.get('/health')
def health():
    return JSONResponse({
        'status': 'ok',
        'service': SERVICE_NAME,
        'timestamp': utc_now_iso()
    })


@app.get('/api/registry/source-distribution')
def get_source_distribution():
    distribution = get_registry_source_distribution()
    return JSONResponse({
        'success': True,
        'timestamp': utc_now_iso(),
        'data': distribution
    })


@app.get('/api/registry/source-by-verdict')
def get_source_by_verdict():
    by_verdict = get_registry_source_by_verdict()
    return JSONResponse({
        'success': True,
        'timestamp': utc_now_iso(),
        'data': by_verdict
    })


@app.get('/api/registry/source-by-risk-tier')
def get_source_by_risk_tier():
    by_risk = get_registry_source_by_risk_tier()
    return JSONResponse({
        'success': True,
        'timestamp': utc_now_iso(),
        'data': by_risk
    })


@app.get('/api/registry/top-sources-by-trust')
def get_top_sources():
    top_sources = get_top_sources_by_trust_score()
    return JSONResponse({
        'success': True,
        'timestamp': utc_now_iso(),
        'data': top_sources
    })


@app.get('/api/registry/summary')
def get_summary():
    total_sql = 'SELECT COUNT(*) as total FROM mcp_server_registry'
    total_result = ws_query(total_sql)
    total_servers = total_result.get('rows', [{}])[0].get('total', 0) if total_result.get('rows') else 0

    sources_sql = 'SELECT COUNT(DISTINCT registry_source) as source_count FROM mcp_server_registry'
    sources_result = ws_query(sources_sql)
    source_count = sources_result.get('rows', [{}])[0].get('source_count', 0) if sources_result.get('rows') else 0

    return JSONResponse({
        'success': True,
        'timestamp': utc_now_iso(),
        'data': {
            'total_servers': total_servers,
            'distinct_sources': source_count,
            'avg_servers_per_source': round(total_servers / source_count, 2) if source_count > 0 else 0
        }
    })


def run():
    logger.info(f'Starting {SERVICE_NAME} on port {PORT}')
    send_heartbeat(status='starting')
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='info')


if __name__ == '__main__':
    if not check_single_instance():
        logger.error('Failed to acquire PID file lock. Exiting.')
        sys.exit(1)
    try:
        run()
    finally:
        remove_pid_file()