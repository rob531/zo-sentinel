import os
import sys
import logging
import signal
import subprocess

PROJECT_DIR = '/home/workspace/zo_sentinel'
sys.path.insert(0, PROJECT_DIR)

SERVICE_NAME = 'snow_integration_smoke'
SERVICE_PORT = None
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
LOG_DIR = '/home/workspace/logs'
LOG_FILE = os.path.join(LOG_DIR, f'{SERVICE_NAME}.log')
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'
QUERY_URL = 'http://127.0.0.1:8772/query'
SNOW_INBOUND_WEBHOOK_PORT = 8780

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(SERVICE_NAME)


def ws_write(table, rows):
    import requests
    payload = {'table': table, 'rows': rows if isinstance(rows, list) else [rows], 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL + '/write', json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql):
    import requests
    resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    return result.get('rows', [])


def ws_execute(sql):
    import requests
    resp = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        old = open(PID_FILE).read().strip()
        if old and old.isdigit():
            try:
                os.kill(int(old), 0)
                log.error('Another instance running with PID %s', old)
                sys.exit(1)
            except OSError:
                pass
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    log.info('Received signal %d, shutting down gracefully', signum)
    remove_pid_file()
    sys.exit(0)


def check_write_service_reachable():
    log.info('Checking write_service reachability...')
    try:
        import requests
        resp = requests.get(WRITE_SERVICE_URL.rsplit('/', 2)[0] + '/health', timeout=5)
        log.info('Write service health: %s', resp.status_code)
        return True
    except Exception as e:
        log.error('Write service not reachable: %s', e)
        return False


def test_snow_connector_import():
    log.info('Testing snow_connector import...')
    try:
        from snow_connector import SnowConnector
        log.info('snow_connector.SnowConnector imported successfully')
        return True
    except ImportError as e:
        log.error('Failed to import snow_connector: %s', e)
        return False


def test_snow_inbound_webhook_reachable():
    log.info('Testing ServiceNow inbound webhook endpoint...')
    import requests
    try:
        resp = requests.get(f'http://127.0.0.1:{SNOW_INBOUND_WEBHOOK_PORT}/health', timeout=5)
        log.info('Snow inbound webhook /health: %s', resp.status_code)
        return True
    except requests.exceptions.ConnectionError:
        log.warning('Snow inbound webhook not reachable on port %d - this may be expected if not started', SNOW_INBOUND_WEBHOOK_PORT)
        return False
    except Exception as e:
        log.error('Snow inbound webhook error: %s', e)
        return False


def test_snow_connector_wiring_import():
    log.info('Testing snow_connector_wiring import...')
    try:
        from snow_connector_wiring import SnowConnectorWiring
        log.info('snow_connector_wiring.SnowConnectorWiring imported successfully')
        return True
    except ImportError as e:
        log.warning('Could not import snow_connector_wiring (may not exist): %s', e)
        try:
            import snow_connector_wiring
            log.info('snow_connector_wiring module imported')
            return True
        except ImportError:
            log.error('Failed to import snow_connector_wiring module: %s', e)
            return False


def test_write_service_query_paths():
    log.info('Testing write_service query paths used by snow_connector...')
    try:
        rows = ws_query('SELECT 1 as test')
        log.info('Query / query path works: %s', rows)
        ws_execute("SELECT 1")
        log.info('Execute path works')
        return True
    except Exception as e:
        log.error('Write service query path failed: %s', e)
        return False


def test_snow_related_tables():
    log.info('Testing snow_connector-related table existence...')
    expected_tables = [
        'mcp_server_registry',
        'approval_workflow_submissions',
        'audit_log'
    ]
    results = {}
    for table in expected_tables:
        try:
            ws_query(f"SELECT COUNT(*) FROM {table} LIMIT 1")
            results[table] = True
            log.info('Table %s exists and is queryable', table)
        except Exception as e:
            results[table] = False
            log.warning('Table %s query failed: %s', table, e)
    return results


def test_snow_connector_integration_import():
    log.info('Testing snow_connector_integration import...')
    try:
        import snow_connector_integration
        log.info('snow_connector_integration module imported successfully')
        return True
    except ImportError as e:
        log.warning('snow_connector_integration not available: %s', e)
        return False


def test_snow_inbound_webhook_import():
    log.info('Testing snow_inbound_webhook import...')
    try:
        import snow_inbound_webhook
        log.info('snow_inbound_webhook module imported successfully')
        return True
    except ImportError as e:
        log.warning('snow_inbound_webhook not available: %s', e)
        return False


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info('=== Snow Integration Smoke Test Starting ===')

    all_passed = True

    log.info('--- Step 1: Write Service Reachability ---')
    if not check_write_service_reachable():
        all_passed = False
        log.error('Write service not reachable - cannot continue smoke test')

    log.info('--- Step 2: snow_connector.py Import ---')
    if not test_snow_connector_import():
        all_passed = False

    log.info('--- Step 3: snow_connector_wiring.py Import ---')
    if not test_snow_connector_wiring_import():
        all_passed = False

    log.info('--- Step 4: snow_connector_integration.py Import ---')
    if not test_snow_connector_integration_import():
        log.warning('snow_connector_integration not found - may be optional')

    log.info('--- Step 5: snow_inbound_webhook.py Import ---')
    if not test_snow_inbound_webhook_import():
        log.warning('snow_inbound_webhook not found - may be optional')

    log.info('--- Step 6: ServiceNow Inbound Webhook Reachability ---')
    if not test_snow_inbound_webhook_reachable():
        all_passed = False

    log.info('--- Step 7: Write Service Query Paths ---')
    if not test_write_service_query_paths():
        all_passed = False

    log.info('--- Step 8: Snow-Related Table Access ---')
    table_results = test_snow_related_tables()
    if not all(table_results.values()):
        all_passed = False

    log.info('--- Step 9: Write Smoke Result to service_health ---')
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
            'status': 'ok' if all_passed else 'degraded',
            'meta': f'snow_connector_smoke_pass={all_passed}'
        })
        log.info('Smoke result written to service_health')
    except Exception as e:
        log.error('Failed to write service_health: %s', e)

    log.info('=== Snow Integration Smoke Test Complete ===')
    log.info('Result: %s', 'PASS' if all_passed else 'FAIL')

    remove_pid_file()
    sys.exit(0 if all_passed else 1)


if __name__ == '__main__':
    run()