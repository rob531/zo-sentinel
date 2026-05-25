#!/usr/bin/env python3
"""
verify_supply_chain_enrichment_wired.py -- ZO-SENTINEL supply chain enrichment verification daemon.
Verifies that verify_supply_chain_enrichment.py is wired correctly and produces distinct score values.
"""

import logging
import time
import requests
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
        'service': 'verify_supply_chain_enrichment_wired',
        'last_heartbeat': datetime.now(timezone.utc).isoformat()
    })

def run():
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger('verify_supply_chain_enrichment_wired')
    log.info('Starting...')
    heartbeat()
    while True:
        try:
            heartbeat()
            scores = ws_query("SELECT DISTINCT score FROM mcp_signal_enrichments WHERE signal_type='supply_chain'")
            if len(scores) > 20:
                log.info(f"Verification passed: {len(scores)} distinct scores found.")
            else:
                log.error(f"Verification failed: Only {len(scores)} distinct scores found. Expected more than 20.")
        except Exception as e:
            log.error('Cycle error: %s', e)
        time.sleep(3600)

if __name__ == '__main__':
    run()