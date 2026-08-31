#!/usr/bin/env python3
import sys
import os
import time
import signal
import logging
import requests
from datetime import datetime, timezone
from typing import Dict, Any, List

SERVICE_NAME = 'signal_analyser_v2_enrichment_wiring'
WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_PORT = None
PID_FILE = '/home/workspace/zo_sentinel/.pids/signal_analyser_v2_enrichment_wiring.pid'
POLL_SECS = 300

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log')]
)
logger = logging.getLogger(__name__)

_pid_file_created = False

def check_single_instance():
    global _pid_file_created
    pid = str(os.getpid())
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                existing = f.read().strip()
            if existing and existing != pid:
                import subprocess
                result = subprocess.run(['ps', '-p', existing, '-o', 'pid='], capture_output=True, text=True)
                if result.returncode == 0:
                    logger.error(f"Another instance running: {existing}")
                    sys.exit(1)
        except Exception:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(pid)
    _pid_file_created = True

def remove_pid_file():
    if _pid_file_created and os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)

def ws_query(sql: str, params: tuple = None) -> List[Dict[str, Any]]:
    payload = {'table': '__query__', 'sql': sql}
    if params:
        payload['params'] = params
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return result.get('rows', [])
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return []

def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    payload = {'table': table, 'rows': rows, 'wait': True}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write failed for {table}: {e}")
        return False

def send_heartbeat(status: str = 'running', meta: Dict[str, Any] = None):
    row = {
        'service_name': SERVICE_NAME,
        'status': status,
        'ts': datetime.now(timezone.utc).isoformat(),
        'meta': json.dumps(meta) if meta else '{}'
    }
    ws_write('service_health', [row])

import json

ENRICHMENT_TYPES = [
    'permission_scope',
    'temporal_stability',
    'tool_description_safety',
    'supply_chain_enrichment',
    'community_signal_enrichment'
]

def cycle() -> Dict[str, Any]:
    results = {
        'enrichment_counts': {},
        'total_servers': 0,
        'servers_with_any_enrichment': 0,
        'coverage_pct': 0.0,
        'wiring_status': {},
        'issues': []
    }
    
    for etype in ENRICHMENT_TYPES:
        sql = f"""
            SELECT COUNT(DISTINCT target_server_id) as cnt
            FROM mcp_signal_enrichments
            WHERE signal_type = ?
        """
        rows = ws_query(sql, (etype,))
        cnt = rows[0]['cnt'] if rows else 0
        results['enrichment_counts'][etype] = cnt
        results['wiring_status'][etype] = 'OK' if cnt > 0 else 'MISSING'
        if cnt == 0:
            results['issues'].append(f"No data for enrichment type: {etype}")
    
    sql_total = "SELECT COUNT(*) as total FROM mcp_server_registry"
    rows = ws_query(sql_total)
    results['total_servers'] = rows[0]['total'] if rows else 0
    
    sql_enriched = """
        SELECT COUNT(DISTINCT target_server_id) as cnt
        FROM mcp_signal_enrichments
    """
    rows = ws_query(sql_enriched)
    results['servers_with_any_enrichment'] = rows[0]['cnt'] if rows else 0
    
    if results['total_servers'] > 0:
        results['coverage_pct'] = round(
            (results['servers_with_any_enrichment'] / results['total_servers']) * 100, 2
        )
    
    sql_scores = "SELECT COUNT(DISTINCT target_server_id) as cnt FROM mcp_signal_scores"
    rows = ws_query(sql_scores)
    scores_count = rows[0]['cnt'] if rows else 0
    
    results['signal_scores_count'] = scores_count
    
    sql_signal = "SELECT COUNT(DISTINCT target_server_id) as cnt FROM mcp_server_signals"
    rows = ws_query(sql_signal)
    signals_count = rows[0]['cnt'] if rows else 0
    
    results['server_signals_count'] = signals_count
    
    for etype in ENRICHMENT_TYPES:
        cnt = results['enrichment_counts'].get(etype, 0)
        logger.info(f"Enrichment [{etype}]: {cnt} servers")
    
    logger.info(f"Coverage: {results['servers_with_any_enrichment']}/{results['total_servers']} = {results['coverage_pct']}%")
    logger.info(f"Signal scores count: {scores_count}, Server signals count: {signals_count}")
    
    if results['issues']:
        for issue in results['issues']:
            logger.warning(f"WIRING ISSUE: {issue}")
        results['wiring_status']['overall'] = 'INCOMPLETE'
    else:
        results['wiring_status']['overall'] = 'COMPLETE'
        logger.info("All enrichment types have data - wiring appears correct")
    
    return results

def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    logger.info(f"Starting {SERVICE_NAME}")
    
    while True:
        try:
            results = cycle()
            send_heartbeat('running', results)
        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)
            send_heartbeat('error', {'error': str(e)})
        
        time.sleep(POLL_SECS)

if __name__ == '__main__':
    run()