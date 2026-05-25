import logging
import requests
import time
import sys
from datetime import datetime, timezone

SERVICE_NAME = 'write_service_diagnostic_probe'
WRITE_SERVICE_URL = 'http://localhost:8772'
TIMEOUT_SECONDS = 10

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(SERVICE_NAME)


def ws_query(sql, params=None):
    payload = {'sql': sql, 'wait': True}
    if params:
        payload['params'] = params
    resp = requests.post(f'{WRITE_SERVICE_URL}/query', json=payload, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def ws_write(table, rows):
    if isinstance(rows, dict):
        rows = [rows]
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(f'{WRITE_SERVICE_URL}/write', json=payload, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def check_health():
    try:
        resp = requests.post(f'{WRITE_SERVICE_URL}/health', json={}, timeout=TIMEOUT_SECONDS)
        if resp.status_code == 200:
            logger.info('Health check PASSED')
            return True
        else:
            logger.warning('Health check returned status: %d', resp.status_code)
            return False
    except Exception as e:
        logger.error('Health check FAILED: %s', e)
        return False


def check_duckdb_access():
    try:
        result = ws_query("SELECT count(*) as table_count FROM information_schema.tables WHERE table_schema = 'memory' OR table_schema = 'temp'")
        logger.info('DuckDB accessible, found tables: %s', result)
        return True
    except Exception as e:
        logger.error('DuckDB access FAILED: %s', e)
        return False


def test_write_throughput():
    probe_id = f'probe_{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")}'
    try:
        ws_write('diagnostic_probe_test', {
            'probe_id': probe_id,
            'ts': datetime.now(timezone.utc).isoformat(),
            'service': SERVICE_NAME
        })
        time.sleep(0.1)
        ws_query("DELETE FROM diagnostic_probe_test WHERE probe_id = ?", [probe_id])
        logger.info('Write/cleanup throughput test PASSED')
        return True
    except Exception as e:
        logger.error('Write throughput test FAILED: %s', e)
        return False


def report_service_health(status, meta):
    try:
        ws_write('service_health', {
            'service_name': SERVICE_NAME,
            'status': status,
            'last_heartbeat': datetime.now(timezone.utc).isoformat(),
            'meta': meta
        })
        logger.info('Reported status %s to service_health', status)
    except Exception as e:
        logger.error('Failed to report to service_health: %s', e)


def main():
    logger.info('Starting write_service diagnostic probe')
    all_passed = True
    meta_parts = []
    
    health_ok = check_health()
    if not health_ok:
        all_passed = False
        meta_parts.append('health_check=FAIL')
    
    duckdb_ok = check_duckdb_access()
    if not duckdb_ok:
        all_passed = False
        meta_parts.append('duckdb_access=FAIL')
    
    throughput_ok = test_write_throughput()
    if not throughput_ok:
        all_passed = False
        meta_parts.append('write_throughput=FAIL')
    
    if all_passed:
        status = 'diagnostic_ok'
        meta = 'health_check=PASS duckdb_access=PASS write_throughput=PASS'
    else:
        status = 'diagnostic_stale'
        meta = ' '.join(meta_parts) if meta_parts else 'unknown_failure'
    
    report_service_health(status, meta)
    
    logger.info('Diagnostic probe complete: %s', status)
    sys.exit(0)


if __name__ == '__main__':
    main()