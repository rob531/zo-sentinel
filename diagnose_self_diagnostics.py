import requests
import logging
from datetime import datetime, timezone, timedelta

WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
STALENESS_THRESHOLD = 600  # seconds

def ws_write(table, rows):
    r = requests.post(WRITE_SERVICE_URL + '/write',
        json={'table': table, 'rows': rows, 'wait': True}, timeout=8)
    return r.status_code == 200

def ws_query(sql):
    r = requests.post(WRITE_SERVICE_URL + '/query', json={'sql': sql}, timeout=8)
    return r.json().get('rows', []) if r.status_code == 200 else []

def heartbeat():
    ws_write('service_health', {
        'service': 'diagnose_self_diagnostics',
        'last_heartbeat': datetime.now(timezone.utc).isoformat()
    })

def check_staleness(service_name):
    threshold_time = datetime.now(timezone.utc) - timedelta(seconds=STALENESS_THRESHOLD)
    sql = f"SELECT last_heartbeat FROM service_health WHERE service='{service_name}' ORDER BY last_heartbeat DESC LIMIT 1"
    result = ws_query(sql)
    if not result:
        return True
    last_heartbeat = datetime.fromisoformat(result[0]['last_heartbeat'])
    return last_heartbeat < threshold_time

def diagnose_staleness():
    services_to_check = ['self_diagnostics', 'threat_intel_ingestor']
    diagnostics_results = []
    for service in services_to_check:
        is_stale = check_staleness(service)
        diagnostics_results.append({
            'service': service,
            'is_stale': is_stale
        })
    ws_write('diagnostics_log', diagnostics_results)

def run():
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger('diagnose_self_diagnostics')
    log.info('Starting...')
    heartbeat()
    while True:
        try:
            heartbeat()
            diagnose_staleness()
        except Exception as e:
            log.error('Cycle error: %s', e)
        time.sleep(3600)

if __name__ == '__main__':
    run()