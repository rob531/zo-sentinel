import os
import sys
import time
import signal
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import requests

SERVICE_NAME = 'permission_scope_enrichment_integration'
SERVICE_PORT = None
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'
LOG_DIR = '/home/workspace/logs'
LOG_FILE = os.path.join(LOG_DIR, f'{SERVICE_NAME}.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(SERVICE_NAME)

os.makedirs(LOG_DIR, exist_ok=True)

sys.path.insert(0, '/home/workspace/zo_sentinel')
try:
    import permission_scope_enrichment
    PERMISSION_SCOPE_MODULE = permission_scope_enrichment
    log.info("Successfully imported permission_scope_enrichment module")
except ImportError as e:
    log.error(f"Failed to import permission_scope_enrichment: {e}")
    PERMISSION_SCOPE_MODULE = None

QUERY_URL = 'http://localhost:8772/query'
WRITE_URL = 'http://localhost:8772/write'


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(WRITE_URL, json={'table': table, 'rows': rows}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed: {e}")
        return False


def send_heartbeat(meta: str = '') -> None:
    ts = datetime.now(timezone.utc).isoformat()
    ws_write('service_health', [{'service': SERVICE_NAME, 'last_heartbeat': ts, 'status': 'running', 'meta': meta}])


def check_single_instance() -> bool:
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        old_pid = int(open(PID_FILE).read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance running with PID {old_pid}")
            return False
        except OSError:
            log.info(f"Stale PID file found, removing")
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    log.info(f"Acquired PID file: {pid}")
    return True


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
        log.info("Removed PID file")


def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def ensure_enrichments_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
        server_id VARCHAR NOT NULL,
        signal_type VARCHAR NOT NULL,
        score DOUBLE,
        confidence DOUBLE,
        evidence VARCHAR,
        computed_at VARCHAR,
        PRIMARY KEY (server_id, signal_type)
    )
    """
    try:
        resp = requests.post(WRITE_URL, json={'sql': sql}, timeout=30)
        log.info("Ensured mcp_signal_enrichments table exists")
    except Exception as e:
        log.error(f"Failed to ensure enrichments table: {e}")


def get_unscored_servers(batch_size: int = 50) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT r.server_id, r.name, r.description, r.metadata
    FROM mcp_server_registry r
    WHERE NOT EXISTS (
        SELECT 1 FROM mcp_signal_enrichments e 
        WHERE e.server_id = r.server_id 
        AND e.signal_type = 'permission_scope'
    )
    LIMIT {batch_size}
    """
    return ws_query(sql)


def parse_metadata(metadata_str: str) -> Dict[str, Any]:
    import json
    if not metadata_str:
        return {}
    try:
        if isinstance(metadata_str, str):
            return json.loads(metadata_str)
        return metadata_str or {}
    except Exception:
        return {}


def compute_permission_scope_score(server: Dict[str, Any]) -> Dict[str, Any]:
    if PERMISSION_SCOPE_MODULE is None:
        return {'score': 0.0, 'confidence': 0.0, 'evidence': '{"error": "module not loaded"}'}
    
    try:
        metadata = parse_metadata(server.get('metadata', ''))
        
        if not metadata:
            metadata = {
                'name': server.get('name', ''),
                'description': server.get('description', '') or ''
            }
        
        result = PERMISSION_SCOPE_MODULE.compute_score(metadata)
        
        return {
            'score': result.get('score', 0.0),
            'confidence': result.get('confidence', 0.0),
            'evidence': result.get('evidence', '{}')
        }
    except Exception as e:
        log.error(f"compute_score failed for {server.get('server_id')}: {e}")
        return {'score': 0.0, 'confidence': 0.0, 'evidence': f'{{"error": "{str(e)}"}}'}


def write_enrichment(server_id: str, signal_type: str, score: float, confidence: float, evidence: str) -> bool:
    ts = datetime.now(timezone.utc).isoformat()
    row = {
        'server_id': server_id,
        'signal_type': signal_type,
        'score': score,
        'confidence': confidence,
        'evidence': evidence,
        'computed_at': ts
    }
    return ws_write('mcp_signal_enrichments', [row])


def get_permission_scope_stats() -> Dict[str, Any]:
    sql = """
    SELECT COUNT(*) as total_enriched,
           COUNT(DISTINCT score) as distinct_scores,
           MIN(score) as min_score,
           MAX(score) as max_score
    FROM mcp_signal_enrichments 
    WHERE signal_type = 'permission_scope'
    """
    rows = ws_query(sql)
    if rows:
        return rows[0]
    return {'total_enriched': 0, 'distinct_scores': 0, 'min_score': 0, 'max_score': 0}


def cycle() -> int:
    processed = 0
    
    ensure_enrichments_table()
    
    unscored = get_unscored_servers(batch_size=50)
    
    if not unscored:
        stats = get_permission_scope_stats()
        log.info(f"No unscored servers. Stats: enriched={stats.get('total_enriched', 0)}, distinct_scores={stats.get('distinct_scores', 0)}")
        return 0
    
    log.info(f"Processing {len(unscored)} unscored servers")
    
    for server in unscored:
        server_id = server.get('server_id')
        if not server_id:
            continue
        
        score_data = compute_permission_scope_score(server)
        
        score = score_data.get('score', 0.0)
        confidence = score_data.get('confidence', 0.0)
        evidence = score_data.get('evidence', '{}')
        
        if write_enrichment(server_id, 'permission_scope', score, confidence, evidence):
            log.debug(f"Written enrichment for {server_id}: score={score:.4f}, confidence={confidence:.4f}")
            processed += 1
        else:
            log.error(f"Failed to write enrichment for {server_id}")
    
    stats = get_permission_scope_stats()
    log.info(f"Cycle complete: processed={processed}, total_enriched={stats.get('total_enriched', 0)}, distinct_scores={stats.get('distinct_scores', 0)}")
    
    return processed


def run() -> None:
    log.info(f"Starting {SERVICE_NAME}")
    
    if not check_single_instance():
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    HEARTBEAT_INTERVAL = 60
    last_heartbeat = time.time()
    
    while True:
        try:
            processed = cycle()
            
            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                meta = f'processed={processed}'
                send_heartbeat(meta)
                last_heartbeat = time.time()
            
            time.sleep(5)
            
        except Exception as e:
            log.error(f"Error in run loop: {e}")
            time.sleep(30)


if __name__ == '__main__':
    run()