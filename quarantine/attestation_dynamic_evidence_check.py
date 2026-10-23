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
        'service': 'attestation_dynamic_evidence_check',
        'last_heartbeat': datetime.now(timezone.utc).isoformat()
    })

def check_attestation_text():
    attestation_texts = ws_query("SELECT id, text FROM attestation_engine")
    findings = []
    for attestation in attestation_texts:
        if not any(keyword in attestation['text'] for keyword in ['current scores', 'dynamic signal']):
            findings.append(attestation['id'])
    return findings

def run():
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger('attestation_dynamic_evidence_check')
    log.info('Starting...')
    heartbeat()
    while True:
        try:
            heartbeat()
            static_text_ids = check_attestation_text()
            if static_text_ids:
                log.warning(f"Static text found in attestation IDs: {static_text_ids}")
                # Propose companion module attestation_dynamic_text.py
                ws_write('audit_log', {
                    'target_server_id': 'attestation_engine',
                    'log_entry': f"Proposed creation of attestation_dynamic_text.py due to static text in IDs: {static_text_ids}",
                    'timestamp': datetime.now(timezone.utc).isoformat()
                })
        except Exception as e:
            log.error('Cycle error: %s', e)
        time.sleep(3600)

if __name__ == '__main__':
    run()