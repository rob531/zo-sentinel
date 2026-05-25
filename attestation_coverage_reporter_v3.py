import requests
import json
import time
import threading
from datetime import datetime, timezone
import os
import sys

SERVICE_NAME = 'attestation_coverage_reporter_v3'
WRITE_SERVICE = 'http://127.0.0.1:8772'
QUERY_URL = f'{WRITE_SERVICE}/query'
WRITE_URL = f'{WRITE_SERVICE}/write'
HEARTBEAT_INTERVAL = 300
LOCKFILE = '/home/workspace/logs/attestation_coverage_reporter_v3.lock'
LOG_DIR = '/home/workspace/logs'
LOG_FILE = os.path.join(LOG_DIR, f'{SERVICE_NAME}.log')

def log(msg):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass

def check_single_instance():
    try:
        os.makedirs(os.path.dirname(LOCKFILE), exist_ok=True)
        if os.path.exists(LOCKFILE):
            with open(LOCKFILE, 'r') as f:
                old_pid = f.read().strip()
            try:
                os.kill(int(old_pid), 0)
                log('Another instance is running. Exiting.')
                return False
            except (OSError, ValueError):
                pass
        with open(LOCKFILE, 'w') as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        log(f'check_single_instance error: {e}')
        return False

def remove_pid_file():
    try:
        if os.path.exists(LOCKFILE):
            os.remove(LOCKFILE)
    except Exception as e:
        log(f'remove_pid_file error: {e}')

def ws_query(sql):
    try:
        resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f'ws_query error: {e}')
        return {'rows': [], 'count': 0}

def ws_write(table, rows):
    try:
        payload = {'table': table, 'rows': rows, 'wait': True}
        resp = requests.post(WRITE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f'ws_write error: {e}')
        return {'ok': False}

def send_heartbeat():
    try:
        ws_write('service_health', {'service': SERVICE_NAME, 'last_heartbeat': datetime.now(timezone.utc).isoformat()})
    except Exception as e:
        log(f'send_heartbeat error: {e}')

def _heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

def _start_heartbeat_thread():
    t = threading.Thread(target=_heartbeat_loop, daemon=True)
    t.start()
    return t

def signal_handler(signum, frame):
    log(f'Received signal {signum}, shutting down')
    remove_pid_file()
    sys.exit(0)

def cycle():
    generated_at = datetime.now(timezone.utc).isoformat()
    
    try:
        result_total = ws_query('SELECT COUNT(*) FROM mcp_server_registry')
        rows_total = result_total.get('rows', [])
        registered_total = rows_total[0]['count_star'] if rows_total else 0
        
        result_attested = ws_query('SELECT COUNT(DISTINCT server_id) FROM mcp_attestations')
        rows_attested = result_attested.get('rows', [])
        attested_distinct = rows_attested[0]['count_distinct'] if rows_attested else 0
        
        coverage_pct = round((attested_distinct / registered_total * 100), 2) if registered_total > 0 else 0.0
        
        result_by_type = ws_query('SELECT attestation_type, COUNT(*) FROM mcp_attestations GROUP BY attestation_type ORDER BY 2 DESC')
        by_type = {}
        for row in result_by_type.get('rows', []):
            by_type[row['attestation_type']] = row['count_star']
        
        result_by_risk = ws_query('SELECT risk_tier, COUNT(*) FROM mcp_server_registry GROUP BY risk_tier')
        by_risk_tier = {}
        for row in result_by_risk.get('rows', []):
            by_risk_tier[row['risk_tier']] = row['count_star']
        
        content = {
            'registered_total': registered_total,
            'attested_distinct': attested_distinct,
            'coverage_pct': coverage_pct,
            'by_type': by_type,
            'by_risk_tier': by_risk_tier,
            'generated_at': generated_at
        }
        
        ws_write('mesh_memory', {
            'agent_id': 'attestation_coverage_reporter_v3',
            'memory_type': 'attestation_coverage_v3',
            'importance': 0.85,
            'content': json.dumps(content),
            'created_at': generated_at
        })
        
        log(f'Cycle complete: registered={registered_total}, attested={attested_distinct}, coverage={coverage_pct}%')
        log(f'By type: {by_type}')
        log(f'By risk tier: {by_risk_tier}')
        
    except Exception as e:
        log(f'cycle error: {e}')

def run():
    if not check_single_instance():
        sys.exit(1)
    
    try:
        import signal
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
    except Exception:
        pass
    
    log(f'{SERVICE_NAME} starting')
    _start_heartbeat_thread()
    
    while True:
        cycle()
        time.sleep(HEARTBEAT_INTERVAL)

if __name__ == '__main__':
    run()