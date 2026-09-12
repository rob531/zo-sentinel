import requests
import logging
from datetime import datetime, timezone

WRITE_SERVICE_URL = 'http://127.0.0.1:8772'

def ws_write(table, rows):
    r = requests.post(WRITE_SERVICE_URL + '/write',
        json={'table': table, 'rows': rows, 'wait': True}, timeout=8)
    return r.status_code == 200

def ws_query(sql):
    r = requests.post(WRITE_SERVICE_URL + '/query', json={'sql': sql}, timeout=8)
    return r.json().get('rows', []) if r.status_code == 200 else []

def heartbeat():
    ws_write('service_health', {
        'service': 'diagnose_smoke_failures_signal',
        'last_heartbeat': datetime.now(timezone.utc).isoformat()
    })

def run():
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger('diagnose_smoke_failures_signal')
    log.info('Starting...')
    heartbeat()
    while True:
        try:
            heartbeat()
            # Check for shared dependencies issues
            log.info('Checking for shared dependency issues...')
            sql = "SELECT * FROM system_dependencies WHERE status='FAILED' AND module IN ('registry_api', 'rug_pull_monitor', 'signal_analyser')"
            failed_dependencies = ws_query(sql)
            if failed_dependencies:
                log.warning(f'Detected failed dependencies: {failed_dependencies}')
                for dep in failed_dependencies:
                    ws_write('dependency_issues_log', {
                        'module': dep['module'],
                        'dependency': dep['dependency_name'],
                        'issue_description': dep['status_message'],
                        'detected_at': datetime.now(timezone.utc).isoformat()
                    })
            else:
                log.info('No shared dependency issues detected.')
        except Exception as e:
            log.error('Cycle error: %s', e)
        time.sleep(3600)

if __name__ == '__main__':
    run()