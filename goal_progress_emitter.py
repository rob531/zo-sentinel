import requests
from datetime import datetime, timezone
import logging
import time

WRITE_SERVICE_URL = 'http://127.0.0.1:8772'

def ws_write(table, rows):
    r = requests.post(WRITE_SERVICE_URL + '/write',
        json={'table': table, 'rows': rows, 'wait': True}, timeout=8)
    return r.status_code == 200

def ws_query(sql):
    r = requests.post(WRITE_SERVICE_URL + '/query', json={'sql': sql}, timeout=8)
    return r.json().get('rows', []) if r.status_code == 200 else []

def get_table_count(table, condition=None):
    sql = f"SELECT COUNT(*) as count FROM {table}"
    if condition:
        sql += f" WHERE {condition}"
    result = ws_query(sql)
    return result[0]['count'] if result else 0

def compute_progress_percentages():
    target_total = 20000
    counts = {
        'mcp_server_registry': get_table_count('mcp_server_registry'),
        'mcp_attestations': get_table_count('mcp_attestations'),
        'mcp_signal_scores': get_table_count('mcp_signal_scores'),
        'mcp_decisions': get_table_count('mcp_decisions'),
        'mcp_fingerprints': get_table_count('mcp_fingerprints'),
        'mcp_discovery_candidates_true': get_table_count('mcp_discovery_candidates', "promoted=TRUE"),
        'mcp_discovery_candidates_false': get_table_count('mcp_discovery_candidates', "promoted=FALSE")
    }
    total_count = sum(counts.values())
    progress_percentages = {k: (v / target_total) * 100 for k, v in counts.items()}
    return progress_percentages

def heartbeat():
    ws_write('service_health', {
        'service': 'goal_progress_emitter',
        'last_heartbeat': datetime.now(timezone.utc).isoformat()
    })

def run():
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger('goal_progress_emitter')
    log.info('Starting...')
    while True:
        try:
            heartbeat()
            progress_percentages = compute_progress_percentages()
            ws_write('service_health', {
                'service': 'goal_progress',
                'last_heartbeat': datetime.now(timezone.utc).isoformat(),
                'progress_percentages': progress_percentages
            })
        except Exception as e:
            log.error('Cycle error: %s', e)
        time.sleep(60)

if __name__ == '__main__':
    run()