import logging
import sys
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import APIRouter, HTTPException

sys.path.insert(0, '/home/workspace/zo_sentinel/')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/definition_history_pipeline_status.log')]
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = f'{WRITE_SERVICE_URL}/query'
EXECUTE_URL = f'{WRITE_SERVICE_URL}/execute'
WRITE_URL = f'{WRITE_SERVICE_URL}/write'
QUERY_TIMEOUT = 30
WRITE_TIMEOUT = 30

router = APIRouter(prefix='/api/definition-history-pipeline', tags=['definition-history-pipeline'])


def ws_query(sql: str) -> list:
    """Execute a SELECT query via write_service."""
    try:
        resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=QUERY_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except requests.RequestException as e:
        logger.error(f'Query failed: {e}')
        return []


def ws_write(table: str, rows: list) -> bool:
    """Write rows to a table via write_service."""
    try:
        resp = requests.post(WRITE_URL, json={'table': table, 'rows': rows}, timeout=WRITE_TIMEOUT)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f'Write failed to {table}: {e}')
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get('/status')
def get_pipeline_status():
    """
    Get overall status of the definition history pipeline.
    Returns daemon health, backfill progress, and pipeline state.
    """
    now = utc_now_iso()
    
    daemon_status = ws_query("""
        SELECT service, last_heartbeat, status, meta
        FROM service_health
        WHERE service LIKE '%definition_history%' OR service LIKE '%mcp_definition_history%'
    """)
    
    pipeline_daemons = []
    for entry in daemon_status:
        service = entry.get('service', '')
        last_heartbeat = entry.get('last_heartbeat', '')
        status = entry.get('status', 'unknown')
        meta = entry.get('meta', {})
        
        heartbeat_age = None
        if last_heartbeat:
            try:
                hb_ts = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))
                age_seconds = (datetime.now(timezone.utc) - hb_ts).total_seconds()
                heartbeat_age = int(age_seconds)
            except Exception:
                heartbeat_age = None
        
        pipeline_daemons.append({
            'service': service,
            'last_heartbeat': last_heartbeat,
            'heartbeat_age_seconds': heartbeat_age,
            'status': status,
            'meta': meta
        })
    
    history_stats = ws_query("""
        SELECT 
            COUNT(*) as total_records,
            COUNT(DISTINCT server_id) as unique_servers,
            MIN(scanned_at) as earliest_record,
            MAX(scanned_at) as latest_record
        FROM mcp_definition_history
    """)
    
    history_count = 0
    unique_servers = 0
    earliest_record = None
    latest_record = None
    
    if history_stats:
        stats = history_stats[0]
        history_count = stats.get('total_records', 0)
        unique_servers = stats.get('unique_servers', 0)
        earliest_record = stats.get('earliest_record')
        latest_record = stats.get('latest_record')
    
    registry_stats = ws_query("""
        SELECT COUNT(*) as total_servers
        FROM mcp_server_registry
    """)
    
    total_registry_servers = 0
    if registry_stats:
        total_registry_servers = registry_stats[0].get('total_servers', 0)
    
    coverage_percent = 0.0
    if total_registry_servers > 0:
        coverage_percent = round((unique_servers / total_registry_servers) * 100, 2)
    
    missing_servers = ws_query("""
        SELECT COUNT(*) as missing_count
        FROM mcp_server_registry r
        WHERE NOT EXISTS (
            SELECT 1 FROM mcp_definition_history h
            WHERE h.server_id = r.server_id
        )
    """)
    
    missing_count = 0
    if missing_servers:
        missing_count = missing_servers[0].get('missing_count', 0)
    
    overall_state = 'idle'
    if pipeline_daemons:
        active_count = sum(1 for d in pipeline_daemons if d.get('heartbeat_age_seconds', 999) < 120)
        if active_count > 0:
            if missing_count > 0:
                overall_state = 'running_backfill'
            else:
                overall_state = 'complete'
        else:
            recent_count = sum(1 for d in pipeline_daemons if d.get('heartbeat_age_seconds', 999) < 3600)
            if recent_count > 0:
                overall_state = 'stale'
            else:
                overall_state = 'stopped'
    
    return {
        'timestamp': now,
        'pipeline_state': overall_state,
        'daemons': pipeline_daemons,
        'backfill_progress': {
            'total_records': history_count,
            'unique_servers_covered': unique_servers,
            'total_registry_servers': total_registry_servers,
            'coverage_percent': coverage_percent,
            'remaining_servers': missing_count,
            'earliest_record': earliest_record,
            'latest_record': latest_record
        },
        'status': 'ok'
    }


@router.get('/daemon/{daemon_name}')
def get_daemon_status(daemon_name: str):
    """Get status for a specific pipeline daemon."""
    result = ws_query(f"""
        SELECT service, last_heartbeat, status, meta
        FROM service_health
        WHERE service = '{daemon_name}'
    """)
    
    if not result:
        raise HTTPException(status_code=404, detail=f'Daemon {daemon_name} not found')
    
    entry = result[0]
    last_heartbeat = entry.get('last_heartbeat', '')
    heartbeat_age = None
    
    if last_heartbeat:
        try:
            hb_ts = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))
            heartbeat_age = int((datetime.now(timezone.utc) - hb_ts).total_seconds())
        except Exception:
            heartbeat_age = None
    
    return {
        'daemon_name': daemon_name,
        'last_heartbeat': last_heartbeat,
        'heartbeat_age_seconds': heartbeat_age,
        'status': entry.get('status', 'unknown'),
        'meta': entry.get('meta', {}),
        'timestamp': utc_now_iso()
    }


@router.get('/backfill/progress')
def get_backfill_progress():
    """Get detailed backfill progress breakdown by date."""
    daily_progress = ws_query("""
        SELECT 
            DATE(scanned_at) as scan_date,
            COUNT(*) as records_that_day,
            COUNT(DISTINCT server_id) as servers_that_day
        FROM mcp_definition_history
        GROUP BY DATE(scanned_at)
        ORDER BY scan_date DESC
        LIMIT 30
    """)
    
    return {
        'daily_progress': daily_progress,
        'timestamp': utc_now_iso()
    }


@router.get('/servers/pending')
def get_pending_servers(limit: int = 100, offset: int = 0):
    """Get list of servers that haven't been scanned into definition history."""
    servers = ws_query(f"""
        SELECT server_id, name, url, verdict, trust_score, last_seen
        FROM mcp_server_registry r
        WHERE NOT EXISTS (
            SELECT 1 FROM mcp_definition_history h
            WHERE h.server_id = r.server_id
        )
        ORDER BY last_seen DESC NULLS LAST
        LIMIT {limit}
        OFFSET {offset}
    """)
    
    total_missing = ws_query("""
        SELECT COUNT(*) as missing_count
        FROM mcp_server_registry r
        WHERE NOT EXISTS (
            SELECT 1 FROM mcp_definition_history h
            WHERE h.server_id = r.server_id
        )
    """)
    
    total_count = 0
    if total_missing:
        total_count = total_missing[0].get('missing_count', 0)
    
    return {
        'servers': servers,
        'total_pending': total_count,
        'limit': limit,
        'offset': offset,
        'timestamp': utc_now_iso()
    }


@router.get('/health')
def health_check():
    """Health check endpoint for the pipeline status router."""
    ws_ok = False
    try:
        resp = requests.get(f'{WRITE_SERVICE_URL}/health', timeout=5)
        ws_ok = resp.status_code == 200
    except requests.RequestException:
        pass
    
    return {
        'status': 'ok' if ws_ok else 'degraded',
        'service': 'definition_history_pipeline_status',
        'write_service_reachable': ws_ok,
        'timestamp': utc_now_iso()
    }


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(router, host='0.0.0.0', port=8789)