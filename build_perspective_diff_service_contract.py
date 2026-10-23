import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/build_perspective_diff_service_contract.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Constants
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
EXECUTE_SERVICE_URL = 'http://localhost:8772/execute'
SERVICE_NAME = 'build_perspective_diff_service_contract'
PROJECT_DIR = '/home/workspace/zo_sentinel'
OUTPUT_FILE = os.path.join(PROJECT_DIR, 'perspective_diff_service.py')


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    payload: Dict[str, Any] = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return True


def ws_execute(sql: str, params: Optional[List[Any]] = None) -> bool:
    payload: Dict[str, Any] = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return True


def compute_diff_id(snapshot_a_id: str, snapshot_b_id: str) -> str:
    import hashlib
    combined = f"{snapshot_a_id}:{snapshot_b_id}"
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def compute_snapshot_diff(
    snapshot_a_id: str,
    snapshot_b_id: str,
    snapshot_a_ts: str,
    snapshot_b_ts: str
) -> Dict[str, Any]:
    diff_id = compute_diff_id(snapshot_a_id, snapshot_b_id)
    diff_ts = utc_now_iso()
    
    query_a = f"""
        SELECT server_id, trust_score, verdict, risk_tier, signal_count
        FROM mcp_risk_register
        WHERE computed_at = '{snapshot_a_ts}'
    """
    query_b = f"""
        SELECT server_id, trust_score, verdict, risk_tier, signal_count
        FROM mcp_risk_register
        WHERE computed_at = '{snapshot_b_ts}'
    """
    
    rows_a = ws_query(query_a)
    rows_b = ws_query(query_b)
    
    map_a = {r['server_id']: r for r in rows_a}
    map_b = {r['server_id']: r for r in rows_b}
    
    all_servers = set(map_a.keys()) | set(map_b.keys())
    
    verdict_changes = []
    trust_score_changes = []
    risk_tier_changes = []
    new_servers = []
    removed_servers = []
    
    for server_id in all_servers:
        reg_a = map_a.get(server_id)
        reg_b = map_b.get(server_id)
        
        if reg_a is None and reg_b is not None:
            new_servers.append(server_id)
        elif reg_a is not None and reg_b is None:
            removed_servers.append(server_id)
        else:
            if reg_a['verdict'] != reg_b['verdict']:
                verdict_changes.append({
                    'server_id': server_id,
                    'from': reg_a['verdict'],
                    'to': reg_b['verdict']
                })
            if reg_a['trust_score'] != reg_b['trust_score']:
                trust_score_changes.append({
                    'server_id': server_id,
                    'from': reg_a['trust_score'],
                    'to': reg_b['trust_score'],
                    'delta': reg_b['trust_score'] - reg_a['trust_score']
                })
            if reg_a['risk_tier'] != reg_b['risk_tier']:
                risk_tier_changes.append({
                    'server_id': server_id,
                    'from': reg_a['risk_tier'],
                    'to': reg_b['risk_tier']
                })
    
    return {
        'diff_id': diff_id,
        'snapshot_a_id': snapshot_a_id,
        'snapshot_b_id': snapshot_b_id,
        'snapshot_a_ts': snapshot_a_ts,
        'snapshot_b_ts': snapshot_b_ts,
        'diff_ts': diff_ts,
        'verdict_changes': verdict_changes,
        'trust_score_changes': trust_score_changes,
        'risk_tier_changes': risk_tier_changes,
        'new_servers': new_servers,
        'removed_servers': removed_servers,
        'total_changes': len(verdict_changes) + len(trust_score_changes) + len(risk_tier_changes),
        'verdict_change_count': len(verdict_changes),
        'trust_score_change_count': len(trust_score_changes),
        'risk_tier_change_count': len(risk_tier_changes),
        'new_server_count': len(new_servers),
        'removed_server_count': len(removed_servers)
    }


def ensure_perspective_diff_table() -> bool:
    sql = """
        CREATE TABLE IF NOT EXISTS perspective_diffs (
            diff_id VARCHAR PRIMARY KEY,
            snapshot_a_id VARCHAR NOT NULL,
            snapshot_b_id VARCHAR NOT NULL,
            snapshot_a_ts TIMESTAMPTZ NOT NULL,
            snapshot_b_ts TIMESTAMPTZ NOT NULL,
            diff_ts TIMESTAMPTZ NOT NULL,
            verdict_changes_json JSON,
            trust_score_changes_json JSON,
            risk_tier_changes_json JSON,
            new_servers_json JSON,
            removed_servers_json JSON,
            total_changes INTEGER DEFAULT 0,
            verdict_change_count INTEGER DEFAULT 0,
            trust_score_change_count INTEGER DEFAULT 0,
            risk_tier_change_count INTEGER DEFAULT 0,
            new_server_count INTEGER DEFAULT 0,
            removed_server_count INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """
    try:
        ws_execute(sql)
        logger.info("Ensured perspective_diffs table exists")
        return True
    except Exception as e:
        logger.error(f"Failed to create perspective_diffs table: {e}")
        return False


def write_diff_record(diff_data: Dict[str, Any]) -> bool:
    row = {
        'diff_id': diff_data['diff_id'],
        'snapshot_a_id': diff_data['snapshot_a_id'],
        'snapshot_b_id': diff_data['snapshot_b_id'],
        'snapshot_a_ts': diff_data['snapshot_a_ts'],
        'snapshot_b_ts': diff_data['snapshot_b_ts'],
        'diff_ts': diff_data['diff_ts'],
        'verdict_changes_json': diff_data['verdict_changes'],
        'trust_score_changes_json': diff_data['trust_score_changes'],
        'risk_tier_changes_json': diff_data['risk_tier_changes'],
        'new_servers_json': diff_data['new_servers'],
        'removed_servers_json': diff_data['removed_servers'],
        'total_changes': diff_data['total_changes'],
        'verdict_change_count': diff_data['verdict_change_count'],
        'trust_score_change_count': diff_data['trust_score_change_count'],
        'risk_tier_change_count': diff_data['risk_tier_change_count'],
        'new_server_count': diff_data['new_server_count'],
        'removed_server_count': diff_data['removed_server_count']
    }
    try:
        ws_write('perspective_diffs', [row])
        logger.info(f"Wrote diff record {diff_data['diff_id']}")
        return True
    except Exception as e:
        logger.error(f"Failed to write diff record: {e}")
        return False


def get_list_snapshots(limit: int = 10) -> List[Dict[str, Any]]:
    sql = f"""
        SELECT DISTINCT computed_at as snapshot_ts,
               COUNT(*) as server_count,
               MIN(computed_at) as first_seen
        FROM mcp_risk_register
        GROUP BY computed_at
        ORDER BY computed_at DESC
        LIMIT {limit}
    """
    return ws_query(sql)


def get_snapshot_servers(snapshot_ts: str) -> List[Dict[str, Any]]:
    sql = f"""
        SELECT server_id, trust_score, verdict, risk_tier, signal_count
        FROM mcp_risk_register
        WHERE computed_at = '{snapshot_ts}'
    """
    return ws_query(sql)


def generate_perspective_diff_service() -> str:
    service_code = '''#!/usr/bin/env python3
"""
Perspective Diff Service - Zo Sentinel
Compares two perspective snapshots to identify changes in trust scores, verdicts, and risk tiers.
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

# Constants
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
EXECUTE_SERVICE_URL = 'http://localhost:8772/execute'
SERVICE_NAME = 'perspective_diff_service'
PORT = 8791
PID_FILE = '/tmp/perspective_diff_service.pid'
POLL_SECS = 300
LOG_DIR = '/home/workspace/logs'
LOG_FILE = os.path.join(LOG_DIR, f'{SERVICE_NAME}.log')

# Ensure log directory
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    payload: Dict[str, Any] = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return True


def ws_execute(sql: str, params: Optional[List[Any]] = None) -> bool:
    payload: Dict[str, Any] = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return True


def check_single_instance() -> None:
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        if old_pid and os.path.exists(f'/proc/{old_pid}'):
            logger.error(f"Service already running as PID {old_pid}")
            sys.exit(1)
        else:
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum: int, frame: Any) -> None:
    logger.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status: str = 'running', meta: Optional[Dict[str, Any]] = None) -> bool:
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': status,
        'meta': meta or {}
    }
    try:
        ws_write('service_health', [row])
        return True
    except Exception as e:
        logger.error(f"Heartbeat failed: {e}")
        return False


def compute_diff_id(snapshot_a_id: str, snapshot_b_id: str) -> str:
    import hashlib
    combined = f"{snapshot_a_id}:{snapshot_b_id}"
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def ensure_perspective_diff_table() -> bool:
    sql = """
        CREATE TABLE IF NOT EXISTS perspective_diffs (
            diff_id VARCHAR PRIMARY KEY,
            snapshot_a_id VARCHAR NOT NULL,
            snapshot_b_id VARCHAR NOT NULL,
            snapshot_a_ts TIMESTAMPTZ NOT NULL,
            snapshot_b_ts TIMESTAMPTZ NOT NULL,
            diff_ts TIMESTAMPTZ NOT NULL,
            verdict_changes_json JSON,
            trust_score_changes_json JSON,
            risk_tier_changes_json JSON,
            new_servers_json JSON,
            removed_servers_json JSON,
            total_changes INTEGER DEFAULT 0,
            verdict_change_count INTEGER DEFAULT 0,
            trust_score_change_count INTEGER DEFAULT 0,
            risk_tier_change_count INTEGER DEFAULT 0,
            new_server_count INTEGER DEFAULT 0,
            removed_server_count INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """
    try:
        ws_execute(sql)
        logger.info("Ensured perspective_diffs table exists")
        return True
    except Exception as e:
        logger.error(f"Failed to create perspective_diffs table: {e}")
        return False


def get_available_snapshots() -> List[Dict[str, Any]]:
    sql = """
        SELECT DISTINCT computed_at as snapshot_ts,
               COUNT(*) as server_count
        FROM mcp_risk_register
        GROUP BY computed_at
        ORDER BY computed_at DESC
        LIMIT 20
    """
    return ws_query(sql)


def get_snapshot_servers(snapshot_ts: str) -> List[Dict[str, Any]]:
    sql = f"""
        SELECT server_id, trust_score, verdict, risk_tier, signal_count
        FROM mcp_risk_register
        WHERE computed_at = '{snapshot_ts}'
    """
    return ws_query(sql)


def compute_snapshot_diff(
    snapshot_a_id: str,
    snapshot_b_id: str,
    snapshot_a_ts: str,
    snapshot_b_ts: str
) -> Dict[str, Any]:
    diff_id = compute_diff_id(snapshot_a_id, snapshot_b_id)
    diff_ts = utc_now_iso()
    
    rows_a = get_snapshot_servers(snapshot_a_ts)
    rows_b = get_snapshot_servers(snapshot_b_ts)
    
    map_a = {r['server_id']: r for r in rows_a}
    map_b = {r['server_id']: r for r in rows_b}
    
    all_servers = set(map_a.keys()) | set(map_b.keys())
    
    verdict_changes = []
    trust_score_changes = []
    risk_tier_changes = []
    new_servers = []
    removed_servers = []
    
    for server_id in all_servers:
        reg_a = map_a.get(server_id)
        reg_b = map_b.get(server_id)
        
        if reg_a is None and reg_b is not None:
            new_servers.append(server_id)
        elif reg_a is not None and reg_b is None:
            removed_servers.append(server_id)
        else:
            if reg_a['verdict'] != reg_b['verdict']:
                verdict_changes.append({
                    'server_id': server_id,
                    'from': reg_a['verdict'],
                    'to': reg_b['verdict']
                })
            if reg_a['trust_score'] != reg_b['trust_score']:
                trust_score_changes.append({
                    'server_id': server_id,
                    'from': reg_a['trust_score'],
                    'to': reg_b['trust_score'],
                    'delta': float(reg_b['trust_score']) - float(reg_a['trust_score'])
                })
            if reg_a['risk_tier'] != reg_b['risk_tier']:
                risk_tier_changes.append({
                    'server_id': server_id,
                    'from': reg_a['risk_tier'],
                    'to': reg_b['risk_tier']
                })
    
    return {
        'diff_id': diff_id,
        'snapshot_a_id': snapshot_a_id,
        'snapshot_b_id': snapshot_b_id,
        'snapshot_a_ts': snapshot_a_ts,
        'snapshot_b_ts': snapshot_b_ts,
        'diff_ts': diff_ts,
        'verdict_changes': verdict_changes,
        'trust_score_changes': trust_score_changes,
        'risk_tier_changes': risk_tier_changes,
        'new_servers': new_servers,
        'removed_servers': removed_servers,
        'total_changes': len(verdict_changes) + len(trust_score_changes) + len(risk_tier_changes),
        'verdict_change_count': len(verdict_changes),
        'trust_score_change_count': len(trust_score_changes),
        'risk_tier_change_count': len(risk_tier_changes),
        'new_server_count': len(new_servers),
        'removed_server_count': len(removed_servers)
    }


def write_diff_record(diff_data: Dict[str, Any]) -> bool:
    row = {
        'diff_id': diff_data['diff_id'],
        'snapshot_a_id': diff_data['snapshot_a_id'],
        'snapshot_b_id': diff_data['snapshot_b_id'],
        'snapshot_a_ts': diff_data['snapshot_a_ts'],
        'snapshot_b_ts': diff_data['snapshot_b_ts'],
        'diff_ts': diff_data['diff_ts'],
        'verdict_changes_json': diff_data['verdict_changes'],
        'trust_score_changes_json': diff_data['trust_score_changes'],
        'risk_tier_changes_json': diff_data['risk_tier_changes'],
        'new_servers_json': diff_data['new_servers'],
        'removed_servers_json': diff_data['removed_servers'],
        'total_changes': diff_data['total_changes'],
        'verdict_change_count': diff_data['verdict_change_count'],
        'trust_score_change_count': diff_data['trust_score_change_count'],
        'risk_tier_change_count': diff_data['risk_tier_change_count'],
        'new_server_count': diff_data['new_server_count'],
        'removed_server_count': diff_data['removed_server_count']
    }
    try:
        ws_write('perspective_diffs', [row])
        logger.info(f"Wrote diff record {diff_data['diff_id']}")
        return True
    except Exception as e:
        logger.error(f"Failed to write diff record: {e}")
        return False


def get_recent_diffs(limit: int = 10) -> List[Dict[str, Any]]:
    sql = f"""
        SELECT diff_id, snapshot_a_id, snapshot_b_id, snapshot_a_ts, snapshot_b_ts,
               diff_ts, total_changes, verdict_change_count, trust_score_change_count,
               risk_tier_change_count, new_server_count, removed_server_count
        FROM perspective_diffs
        ORDER BY diff_ts DESC
        LIMIT {limit}
    """
    return ws_query(sql)


def get_diff_detail(diff_id: str) -> Optional[Dict[str, Any]]:
    sql = f"SELECT * FROM perspective_diffs WHERE diff_id = '{diff_id}'"
    rows = ws_query(sql)
    return rows[0] if rows else None


def cycle() -> Dict[str, Any]:
    """Perform one diff cycle: compare latest available snapshots."""
    logger.info("Starting diff cycle")
    
    ensure_perspective_diff_table()
    
    snapshots = get_available_snapshots()
    
    if len(snapshots) < 2:
        logger.info(f"Not enough snapshots for diffing (found {len(snapshots)})")
        return {'status': 'skipped', 'reason': 'insufficient_snapshots', 'count': len(snapshots)}
    
    snapshot_b = snapshots[0]
    snapshot_a = snapshots[1]
    
    diff_data = compute_snapshot_diff(
        snapshot_a_id=snapshot_a['snapshot_ts'],
        snapshot_b_id=snapshot_b['snapshot_ts'],
        snapshot_a_ts=snapshot_a['snapshot_ts'],
        snapshot_b_ts=snapshot_b['snapshot_ts']
    )
    
    write_diff_record(diff_data)
    
    recent_diffs = get_recent_diffs(5)
    
    result = {
        'status': 'success',
        'diff_id': diff_data['diff_id'],
        'snapshot_a_ts': snapshot_a['snapshot_ts'],
        'snapshot_b_ts': snapshot_b['snapshot_ts'],
        'total_changes': diff_data['total_changes'],
        'verdict_changes': diff_data['verdict_change_count'],
        'trust_score_changes': diff_data['trust_score_change_count'],
        'risk_tier_changes': diff_data['risk_tier_change_count'],
        'new_servers': diff_data['new_server_count'],
        'removed_servers': diff_data['removed_server_count'],
        'recent_diffs_count': len(recent_diffs)
    }
    
    logger.info(f"Diff cycle complete: {result}")
    return result


def run() -> None:
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    check_single_instance()
    logger.info(f"Starting {SERVICE_NAME}")
    
    ensure_perspective_diff_table()
    
    while True:
        try:
            result = cycle()
            send_heartbeat(status='running', meta=result)
        except Exception as e:
            logger.error(f"Cycle failed: {e}")
            send_heartbeat(status='error', meta={'error': str(e)})
        
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()
'''
    return service_code


def build_perspective_diff_service_contract() -> bool:
    logger.info("Building perspective_diff_service.py")
    
    try:
        service_code = generate_perspective_diff_service()
        
        with open(OUTPUT_FILE, 'w') as f:
            f.write(service_code)
        
        logger.info(f"Wrote {OUTPUT_FILE}")
        
        os.chmod(OUTPUT_FILE, 0o755)
        
        return True
    
    except Exception as e:
        logger.error(f"Build failed: {e}")
        return False


def verify_contract() -> Dict[str, Any]:
    logger.info("Verifying contract implementation")
    
    checks = {
        'file_exists': os.path.exists(OUTPUT_FILE),
        'has_ws_query': False,
        'has_ws_write': False,
        'has_heartbeat': False,
        'has_diff_logic': False,
        'has_snapshot_comparison': False
    }
    
    if checks['file_exists']:
        with open(OUTPUT_FILE, 'r') as f:
            content = f.read()
        
        checks['has_ws_query'] = 'def ws_query' in content
        checks['has_ws_write'] = 'def ws_write' in content
        checks['has_heartbeat'] = 'def send_heartbeat' in content
        checks['has_diff_logic'] = 'compute_snapshot_diff' in content
        checks['has_snapshot_comparison'] = 'snapshot_a' in content and 'snapshot_b' in content
    
    all_passed = all(checks.values())
    checks['all_passed'] = all_passed
    
    logger.info(f"Contract verification: {checks}")
    return checks


def main() -> int:
    logger.info("=" * 60)
    logger.info("Perspective Diff Service Contract Builder")
    logger.info("=" * 60)
    
    ensure_perspective_diff_table()
    
    if not build_perspective_diff_service_contract():
        logger.error("Build failed")
        return 1
    
    checks = verify_contract()
    
    if not checks['all_passed']:
        logger.error(f"Contract verification failed: {checks}")
        return 1
    
    logger.info("=" * 60)
    logger.info("Build complete - perspective_diff_service.py generated")
    logger.info("=" * 60)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())