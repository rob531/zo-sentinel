import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
SERVICE_NAME = 'registry_quick_search'
PORT = 8782
PID_FILE = '/tmp/registry_quick_search.pid'
LOG_FILE = '/home/workspace/logs/registry_quick_search.log'
POLL_SECS = 60

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(__name__)

_process_start: Optional[float] = None


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
        log.error('ws_query failed: %s', e)
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error('ws_write failed: %s', e)
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
        log.error('ws_execute failed: %s', e)
        return False


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error('Another instance is running with PID %s', old_pid)
            return False
        except (OSError, ValueError):
            log.warning('Stale PID file found, removing')
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file() -> None:
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def signal_handler(signum: int, frame: Any) -> None:
    log.info('Received signal %d, shutting down', signum)
    remove_pid_file()
    sys.exit(0)


def send_heartbeat() -> None:
    global _process_start
    if _process_start is None:
        _process_start = time.time()
    uptime = int(time.time() - _process_start)
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': 'ok',
        'meta': f'uptime={uptime}'
    }
    ws_write('service_health', [row])


def ensure_search_cache_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS registry_quick_search_cache (
        query_hash VARCHAR,
        query_text VARCHAR,
        results_json VARCHAR,
        server_count INTEGER,
        created_at TIMESTAMPTZ,
        expires_at TIMESTAMPTZ,
        PRIMARY KEY (query_hash)
    )
    """
    ws_execute(sql)


def compute_query_hash(query_text: str) -> str:
    import hashlib
    normalized = query_text.lower().strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]


def search_servers(query: str, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    if not query or len(query) < 2:
        return {'servers': [], 'total': 0, 'cached': False}
    
    query_hash = compute_query_hash(query)
    cached = ws_query(
        "SELECT results_json, server_count, expires_at FROM registry_quick_search_cache WHERE query_hash = ? AND expires_at > ?",
        [query_hash, utc_now_iso()]
    )
    if cached:
        import json
        try:
            results = json.loads(cached[0]['results_json'])
            return {'servers': results, 'total': cached[0]['server_count'], 'cached': True}
        except (json.JSONDecodeError, KeyError):
            pass
    
    search_term = f'%{query}%'
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
        last_seen
    FROM mcp_server_registry
    WHERE 
        name ILIKE ? 
        OR description ILIKE ?
        OR url ILIKE ?
    ORDER BY 
        CASE WHEN name ILIKE ? THEN 0 ELSE 1 END,
        trust_score DESC NULLS LAST,
        scan_count DESC NULLS LAST
    LIMIT ? OFFSET ?
    """
    servers = ws_query(sql, [search_term, search_term, search_term, search_term, limit, offset])
    
    count_sql = """
    SELECT COUNT(*) as cnt FROM mcp_server_registry
    WHERE name ILIKE ? OR description ILIKE ? OR url ILIKE ?
    """
    count_result = ws_query(count_sql, [search_term, search_term, search_term])
    total = count_result[0]['cnt'] if count_result else 0
    
    import json
    results_json = json.dumps(servers)
    expires_at = utc_now_iso()
    import datetime as dt
    expires_dt = datetime.now(timezone.utc) + dt.timedelta(minutes=15)
    expires_at = expires_dt.isoformat()
    
    ws_execute(
        """
        INSERT OR REPLACE INTO registry_quick_search_cache 
        (query_hash, query_text, results_json, server_count, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [query_hash, query, results_json, total, utc_now_iso(), expires_at]
    )
    
    return {'servers': servers, 'total': total, 'cached': False}


def get_trending_servers(limit: int = 20) -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        server_id,
        name,
        url,
        description,
        trust_score,
        verdict,
        scan_count
    FROM mcp_server_registry
    WHERE scan_count > 0
    ORDER BY scan_count DESC
    LIMIT ?
    """
    return ws_query(sql, [limit])


def get_recent_servers(limit: int = 20) -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        server_id,
        name,
        url,
        description,
        trust_score,
        verdict,
        first_seen
    FROM mcp_server_registry
    ORDER BY first_seen DESC NULLS LAST
    LIMIT ?
    """
    return ws_query(sql, [limit])


def get_servers_by_verdict(verdict: str, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    sql = """
    SELECT 
        server_id,
        name,
        url,
        description,
        trust_score,
        verdict,
        registry_source,
        scan_count
    FROM mcp_server_registry
    WHERE verdict = ?
    ORDER BY trust_score DESC NULLS LAST, scan_count DESC NULLS LAST
    LIMIT ? OFFSET ?
    """
    servers = ws_query(sql, [verdict, limit, offset])
    
    count_sql = "SELECT COUNT(*) as cnt FROM mcp_server_registry WHERE verdict = ?"
    count_result = ws_query(count_sql, [verdict])
    total = count_result[0]['cnt'] if count_result else 0
    
    return {'servers': servers, 'total': total}


def get_servers_by_risk_tier(risk_tier: str, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    sql = """
    SELECT 
        r.server_id,
        r.name,
        r.url,
        r.description,
        r.trust_score,
        r.verdict,
        r.scan_count,
        rr.risk_tier,
        rr.risk_rank
    FROM mcp_server_registry r
    LEFT JOIN mcp_risk_register rr ON r.server_id = rr.server_id
    WHERE rr.risk_tier = ?
    ORDER BY rr.risk_rank ASC NULLS LAST
    LIMIT ? OFFSET ?
    """
    servers = ws_query(sql, [risk_tier, limit, offset])
    
    count_sql = """
    SELECT COUNT(*) as cnt 
    FROM mcp_server_registry r
    LEFT JOIN mcp_risk_register rr ON r.server_id = rr.server_id
    WHERE rr.risk_tier = ?
    """
    count_result = ws_query(count_sql, [risk_tier])
    total = count_result[0]['cnt'] if count_result else 0
    
    return {'servers': servers, 'total': total}


def cleanup_expired_cache() -> int:
    deleted = ws_execute(
        "DELETE FROM registry_quick_search_cache WHERE expires_at < ?",
        [utc_now_iso()]
    )
    return 0


def health() -> Dict[str, Any]:
    global _process_start
    uptime = int(time.time() - _process_start) if _process_start else 0
    return {
        'service': SERVICE_NAME,
        'status': 'ok',
        'uptime': uptime
    }


def cycle() -> None:
    log.info('Running search cycle')
    cleanup_expired_cache()
    send_heartbeat()


def run() -> None:
    global _process_start
    _process_start = time.time()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        log.error('Failed to acquire lock, exiting')
        sys.exit(1)
    
    log.info('Starting %s', SERVICE_NAME)
    
    ensure_search_cache_table()
    
    try:
        while True:
            cycle()
            time.sleep(POLL_SECS)
    except Exception as e:
        log.exception('Fatal error in run loop: %s', e)
    finally:
        remove_pid_file()


if __name__ == '__main__':
    run()