import sys
import time
import signal
import logging
from typing import List, Dict, Any, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
LOG = logging.getLogger('enrichment_wiring_weak_signals')

SERVICE_NAME = 'enrichment_wiring_weak_signals'
SERVICE_PORT = 8792
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
POLL_SECS = 300
HEARTBEAT_INTERVAL = 60

WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'
EXECUTE_SERVICE_URL = 'http://127.0.0.1:8772/execute'

ENRICHMENT_MODULES = {
    'temporal_stability': {
        'module': 'temporal_stability_enrichment_v4',
        'signal_name': 'temporal_stability',
        'version': 'v4'
    },
    'tool_description_safety': {
        'module': 'tool_description_safety_enrichment_v4',
        'signal_name': 'tool_description_safety',
        'version': 'v4'
    },
    'permission_scope': {
        'module': 'permission_scope_enrichment_v3',
        'signal_name': 'permission_scope',
        'version': 'v3'
    }
}

_cached_modules: Dict[str, Any] = {}
_start_time = time.time()
_running = True


def signal_handler(signum, frame):
    global _running
    LOG.info(f'Received signal {signum}, shutting down...')
    _running = False


def remove_pid_file():
    try:
        import os
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        LOG.warning(f'Error removing PID file: {e}')


def check_single_instance() -> bool:
    import os
    import threading
    pid = os.getpid()
    lock = threading.Lock()
    with lock:
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE, 'r') as f:
                    old_pid = int(f.read().strip())
                if old_pid != pid:
                    import psutil
                    if psutil.pid_exists(old_pid):
                        LOG.error(f'Another instance already running with PID {old_pid}')
                        return False
                    else:
                        LOG.warning(f'Old PID file found but process not running, removing')
                        os.remove(PID_FILE)
            except (ValueError, psutil.NoSuchProcess):
                if os.path.exists(PID_FILE):
                    os.remove(PID_FILE)
        with open(PID_FILE, 'w') as f:
            f.write(str(pid))
        return True


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_query_url() -> str:
    return QUERY_SERVICE_URL


def get_execute_url() -> str:
    return EXECUTE_SERVICE_URL


def ws_write(table: str, rows: List[Dict[str, Any]], wait: bool = True) -> bool:
    url = get_write_url()
    payload = {'table': table, 'rows': rows, 'wait': wait}
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        LOG.error(f'Write failed for table {table}: {e}')
        return False


def ws_query(sql: str) -> List[Dict[str, Any]]:
    url = get_query_url()
    payload = {'sql': sql}
    try:
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        LOG.error(f'Query failed: {sql[:100]}... Error: {e}')
        return []


def ws_execute(sql: str) -> bool:
    url = get_execute_url()
    payload = {'sql': sql}
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        LOG.error(f'Execute failed: {sql[:100]}... Error: {e}')
        return False


def send_heartbeat() -> bool:
    url = get_write_url()
    payload = {
        'table': 'service_health',
        'rows': [{'service': SERVICE_NAME, 'last_heartbeat': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}],
        'wait': True
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        LOG.warning(f'Heartbeat failed: {e}')
        return False


def load_enrichment_module(module_name: str) -> Optional[Any]:
    if module_name in _cached_modules:
        return _cached_modules[module_name]
    try:
        if module_name in sys.modules:
            mod = sys.modules[module_name]
            _cached_modules[module_name] = mod
            return mod
        import importlib
        mod = importlib.import_module(module_name)
        _cached_modules[module_name] = mod
        LOG.info(f'Successfully loaded module: {module_name}')
        return mod
    except Exception as e:
        LOG.error(f'Failed to load module {module_name}: {e}')
        return None


def ensure_enrichments_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
        server_id VARCHAR,
        signal_name VARCHAR,
        signal_version VARCHAR,
        score DOUBLE,
        evidence VARCHAR,
        computed_at TIMESTAMP,
        PRIMARY KEY (server_id, signal_name, signal_version)
    )
    """
    return ws_execute(sql)


def get_all_servers() -> List[Dict[str, Any]]:
    sql = "SELECT server_id, name, url, description FROM mcp_server_registry"
    return ws_query(sql)


def compute_and_write_enrichment(
    module_name: str,
    signal_name: str,
    signal_version: str,
    servers: List[Dict[str, Any]]
) -> int:
    mod = load_enrichment_module(module_name)
    if not mod:
        LOG.error(f'Module {module_name} not available')
        return 0

    if not hasattr(mod, 'compute_score'):
        LOG.error(f'Module {module_name} missing compute_score function')
        return 0

    compute_score_fn = mod.compute_score

    rows_to_write = []
    computed_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    success_count = 0

    for server in servers:
        server_id = server.get('server_id')
        if not server_id:
            continue
        try:
            score = compute_score_fn(server)
            evidence = ''
            if hasattr(mod, 'get_evidence'):
                evidence = mod.get_evidence(server) or ''
            elif hasattr(mod, 'compute_batch_scores'):
                batch = mod.compute_batch_scores([server])
                if batch:
                    evidence = str(batch[0].get('evidence', ''))

            rows_to_write.append({
                'server_id': server_id,
                'signal_name': signal_name,
                'signal_version': signal_version,
                'score': score if score is not None else 0.0,
                'evidence': evidence if evidence else '',
                'computed_at': computed_at
            })
            success_count += 1

            if len(rows_to_write) >= 100:
                if ws_write('mcp_signal_enrichments', rows_to_write):
                    rows_to_write = []
                else:
                    LOG.error(f'Failed to write batch for {signal_name}')
                    return success_count

        except Exception as e:
            LOG.warning(f'Error computing score for server {server_id}: {e}')
            continue

    if rows_to_write:
        if not ws_write('mcp_signal_enrichments', rows_to_write):
            LOG.error(f'Failed to write final batch for {signal_name}')
        else:
            LOG.info(f'Wrote {len(rows_to_write)} rows for {signal_name}')

    return success_count


def check_enrichment_coverage(signal_name: str) -> Dict[str, Any]:
    sql = f"""
    SELECT 
        COUNT(DISTINCT server_id) as covered_count,
        COUNT(DISTINCT score) as distinct_scores
    FROM mcp_signal_enrichments 
    WHERE signal_name = '{signal_name}'
    """
    result = ws_query(sql)
    if result:
        return result[0]
    return {'covered_count': 0, 'distinct_scores': 0}


def run_enrichment_cycle() -> Dict[str, Any]:
    results = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'enrichments': {}
    }

    LOG.info('Starting enrichment cycle...')
    servers = get_all_servers()
    total_servers = len(servers)
    LOG.info(f'Processing {total_servers} servers')

    if not servers:
        LOG.warning('No servers found in registry')
        return results

    for key, config in ENRICHMENT_MODULES.items():
        module_name = config['module']
        signal_name = config['signal_name']
        signal_version = config['version']

        before = check_enrichment_coverage(signal_name)
        LOG.info(f'[{signal_name}] Before: {before.get("covered_count", 0)} servers, {before.get("distinct_scores", 0)} distinct scores')

        count = compute_and_write_enrichment(module_name, signal_name, signal_version, servers)

        after = check_enrichment_coverage(signal_name)
        LOG.info(f'[{signal_name}] After: {after.get("covered_count", 0)} servers, {after.get("distinct_scores", 0)} distinct scores')

        results['enrichments'][key] = {
            'signal_name': signal_name,
            'servers_processed': count,
            'total_servers': total_servers,
            'coverage_before': before,
            'coverage_after': after
        }

    LOG.info('Enrichment cycle complete')
    return results


def heartbeat_loop():
    last_heartbeat = time.time()
    while _running:
        now = time.time()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            send_heartbeat()
            last_heartbeat = now
        time.sleep(5)


def run():
    global _running

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    LOG.info(f'Starting {SERVICE_NAME}...')

    if not check_single_instance():
        LOG.error('Failed to acquire PID lock, exiting')
        return

    if not ensure_enrichments_table():
        LOG.error('Failed to ensure mcp_signal_enrichments table exists')
        remove_pid_file()
        return

    send_heartbeat()

    import threading
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    LOG.info(f'{SERVICE_NAME} running on port {SERVICE_PORT}')
    LOG.info(f'Heartbeat interval: {HEARTBEAT_INTERVAL}s, Poll interval: {POLL_SECS}s')

    while _running:
        try:
            run_enrichment_cycle()
        except Exception as e:
            LOG.error(f'Error in enrichment cycle: {e}')

        for _ in range(POLL_SECS):
            if not _running:
                break
            time.sleep(1)

    LOG.info(f'{SERVICE_NAME} shutting down...')
    remove_pid_file()
    LOG.info('Shutdown complete')


if __name__ == '__main__':
    run()