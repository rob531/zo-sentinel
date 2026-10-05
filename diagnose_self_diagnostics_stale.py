import os
import sys
import logging
import requests
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

SERVICE_NAME = 'diagnose_self_diagnostics_stale'
SERVICE_PORT = None
WRITE_SERVICE_URL = 'http://localhost:8772'
PID_FILE = None
LOG_FILE = '/home/workspace/logs/diagnose_self_diagnostics_stale.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(__name__)

DIAGNOSTIC_THRESHOLD_SECONDS = 600
TARGET_DAEMON = 'self_diagnostics'
DAEMON_LOG_PATH = '/home/workspace/logs/self_diagnostics.log'
DB_PATH = '/home/workspace/Datasets/zo-mesh/mesh_memory.db'


def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL + '/write', json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql, params=None):
    payload = {'sql': sql, 'params': params} if params else {'sql': sql}
    resp = requests.post(WRITE_SERVICE_URL + '/query', json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def check_write_service_connectivity():
    try:
        resp = requests.post(WRITE_SERVICE_URL + '/query', json={'sql': 'SELECT 1'}, timeout=10)
        if resp.status_code == 200:
            return {'ok': True, 'latency_ms': resp.elapsed.total_seconds() * 1000}
        return {'ok': False, 'error': f'HTTP {resp.status_code}'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def check_daemon_log_for_startup_and_exceptions():
    issues = []
    if not os.path.exists(DAEMON_LOG_PATH):
        return {
            'log_exists': False,
            'last_modified': None,
            'startup_ts': None,
            'exceptions': [],
            'issues': ['Daemon log file not found']
        }
    stat = os.stat(DAEMON_LOG_PATH)
    last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    with open(DAEMON_LOG_PATH, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    recent_lines = lines[-200:]
    exceptions = []
    startup_ts = None
    for line in recent_lines:
        if 'Starting self_diagnostics' in line or 'Entering run()' in line or 'self_diagnostics run loop started' in line:
            try:
                startup_ts = line.split('[')[0].strip()[:27]
            except Exception:
                pass
        if 'Exception' in line or 'Error:' in line or 'Traceback' in line:
            exceptions.append(line.strip()[:200])
    return {
        'log_exists': True,
        'last_modified': last_modified,
        'last_line': lines[-1].strip()[:200] if lines else None,
        'startup_ts': startup_ts,
        'exceptions': exceptions[:5],
        'issues': issues
    }


def check_scheduled_run_table():
    try:
        result = ws_query(
            "SELECT MAX(last_heartbeat) as last_hb, COUNT(*) as total FROM service_health WHERE service_name = 'self_diagnostics'"
        )
        rows = result.get('rows', [])
        if rows:
            row = rows[0]
            last_hb = row.get('last_hb') or row.get('last_hb')
            total = row.get('total', 0)
            return {'query_ok': True, 'last_heartbeat': last_hb, 'total_entries': total}
        return {'query_ok': True, 'last_heartbeat': None, 'total_entries': 0}
    except Exception as e:
        return {'query_ok': False, 'error': str(e)}


def check_process_alive():
    try:
        import subprocess
        result = subprocess.run(
            ['pgrep', '-f', '/home/workspace/zo_sentinel/self_diagnostics.py'],
            capture_output=True, text=True, timeout=5
        )
        pids = result.stdout.strip().split('\n') if result.stdout.strip() else []
        pids = [p for p in pids if p]
        if pids:
            return {'alive': True, 'pids': pids}
        return {'alive': False, 'pids': []}
    except Exception as e:
        return {'alive': False, 'error': str(e)}


def check_self_diagnostics_schedule():
    try:
        result = ws_query(
            "SELECT MAX(last_heartbeat) as last_hb, COUNT(*) as cnt FROM service_health WHERE service_name = 'self_diagnostics'"
        )
        rows = result.get('rows', [])
        if rows:
            row = rows[0]
            return {
                'table_checked': 'service_health',
                'last_heartbeat': row.get('last_hb'),
                'entry_count': row.get('cnt', 0)
            }
        return {'table_checked': 'service_health', 'last_heartbeat': None, 'entry_count': 0}
    except Exception as e:
        return {'table_checked': 'service_health', 'error': str(e)}


def compute_staleness(last_hb_iso):
    if not last_hb_iso:
        return None
    try:
        if last_hb_iso.endswith('Z'):
            last_hb = datetime.fromisoformat(last_hb_iso.replace('Z', '+00:00'))
        else:
            last_hb = datetime.fromisoformat(last_hb_iso)
        now = datetime.now(timezone.utc)
        return (now - last_hb).total_seconds()
    except Exception:
        return None


def build_diagnostic_id(report):
    content = str(report['timestamp']) + TARGET_DAEMON + str(report['conclusion']['stale'])
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def run():
    logger.info('Starting self_diagnostics stale diagnostic')
    ts = datetime.now(timezone.utc).isoformat()

    ws_conn = check_write_service_connectivity()
    logger.info(f'write_service connectivity: {ws_conn}')

    log_info = check_daemon_log_for_startup_and_exceptions()
    logger.info(f'Daemon log info: {log_info}')

    process_info = check_process_alive()
    logger.info(f'Process alive check: {process_info}')

    schedule_info = check_self_diagnostics_schedule()
    logger.info(f'scheduled_run/service_health info: {schedule_info}')

    last_hb = schedule_info.get('last_heartbeat') or log_info.get('startup_ts')
    staleness = compute_staleness(last_hb) if last_hb else None

    if staleness is not None:
        is_stale = staleness > DIAGNOSTIC_THRESHOLD_SECONDS
        conclusion = {
            'stale': is_stale,
            'staleness_seconds': round(staleness, 1),
            'threshold_seconds': DIAGNOSTIC_THRESHOLD_SECONDS,
            'message': f'Daemon stale for {staleness:.0f}s (threshold: {DIAGNOSTIC_THRESHOLD_SECONDS}s)' if is_stale else f'Daemon healthy, last heartbeat {staleness:.0f}s ago'
        }
    else:
        conclusion = {
            'stale': True,
            'staleness_seconds': None,
            'threshold_seconds': DIAGNOSTIC_THRESHOLD_SECONDS,
            'message': 'Cannot determine staleness: no heartbeat found'
        }

    diagnostic_id = build_diagnostic_id({'timestamp': ts, 'conclusion': conclusion})
    report = {
        'diagnostic_id': diagnostic_id,
        'target_daemon': TARGET_DAEMON,
        'timestamp': ts,
        'write_service': ws_conn,
        'daemon_log': log_info,
        'process': process_info,
        'scheduled_run': schedule_info,
        'staleness_seconds': staleness,
        'conclusion': conclusion
    }

    try:
        ws_write('diagnostic_reports', [report])
        logger.info(f'Diagnostic report written: {diagnostic_id}')
    except Exception as e:
        logger.error(f'Failed to write diagnostic report: {e}')

    logger.info(f'Diagnostic complete: {conclusion["message"]}')
    return report


if __name__ == '__main__':
    result = run()
    print('DIAGNOSTIC_REPORT:', result)
    sys.exit(0)