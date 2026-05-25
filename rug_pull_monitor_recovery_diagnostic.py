import logging
import os
import requests
import sys
from datetime import datetime, timedelta, timezone

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'rug_pull_monitor_recovery_diagnostic'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

RUG_PULL_MONITOR_PID_FILE = '/home/workspace/zo_sentinel/rug_pull_monitor.pid'
RUG_PULL_MONITOR_SOURCE = '/home/workspace/zo_sentinel/rug_pull_monitor.py'
HEARTBEAT_THRESHOLD_HOURS = 600

def ws_query(sql):
    payload = {'sql': sql}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get('rows', [])

def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def check_heartbeat_status():
    sql = f"""
    SELECT service, last_heartbeat, status, meta
    FROM service_health
    WHERE service = 'rug_pull_monitor'
    ORDER BY ts DESC
    LIMIT 5
    """
    try:
        rows = ws_query(sql)
        if not rows:
            logger.warning("No service_health rows found for rug_pull_monitor")
            return None
        return rows
    except Exception as e:
        logger.error(f"Failed to query service_health: {e}")
        return None

def get_last_heartbeat_age_hours(rows):
    if not rows:
        return None
    latest = rows[0]
    last_heartbeat_str = latest.get('last_heartbeat')
    if not last_heartbeat_str:
        return None
    try:
        if last_heartbeat_str.endswith('Z'):
            last_heartbeat_str = last_heartbeat_str[:-1]
        last_ts = datetime.fromisoformat(last_heartbeat_str).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age_hours = (now - last_ts).total_seconds() / 3600
        return age_hours
    except Exception as e:
        logger.error(f"Failed to parse last_heartbeat: {e}")
        return None

def check_pid_file():
    if os.path.exists(RUG_PULL_MONITOR_PID_FILE):
        try:
            with open(RUG_PULL_MONITOR_PID_FILE, 'r') as f:
                pid = f.read().strip()
            logger.info(f"PID file exists with PID: {pid}")
            return {'exists': True, 'pid': pid}
        except Exception as e:
            logger.error(f"Failed to read PID file: {e}")
            return {'exists': True, 'pid': None, 'error': str(e)}
    else:
        logger.warning("PID file does not exist")
        return {'exists': False}

def check_process_running(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False

def check_supervisord_status():
    supervisord_dir = '/tmp'
    markers = [
        '/tmp/supervisor_rug_pull_monitor.sock',
        '/tmp/rug_pull_monitor.supervisor',
        '/tmp/supervisord.sock'
    ]
    found = []
    for marker in markers:
        if os.path.exists(marker):
            found.append(marker)
    return found

def read_source_for_heartbeat_issues():
    if not os.path.exists(RUG_PULL_MONITOR_SOURCE):
        logger.error(f"Source file not found: {RUG_PULL_MONITOR_SOURCE}")
        return {'error': 'source_not_found'}
    try:
        with open(RUG_PULL_MONITOR_SOURCE, 'r') as f:
            content = f.read()
        issues = []
        if 'send_heartbeat' not in content:
            issues.append('missing send_heartbeat call')
        if 'ws_write' not in content and 'write_service' not in content:
            issues.append('missing write_service integration')
        if 'cycle()' not in content:
            issues.append('missing cycle() function pattern')
        if 'while' not in content and 'run()' not in content:
            issues.append('missing main loop pattern')
        return {'issues': issues, 'has_source': True}
    except Exception as e:
        logger.error(f"Failed to read source: {e}")
        return {'error': str(e)}

def write_diagnostic_result(diagnosis, details):
    ts = datetime.now(timezone.utc).isoformat() + 'Z'
    rows = [{
        'service': SERVICE_NAME,
        'ts': ts,
        'status': 'diagnostic_complete',
        'meta': {'diagnosis': diagnosis, 'details': details}
    }]
    try:
        ws_write('service_health', rows)
    except Exception as e:
        logger.error(f"Failed to write diagnostic result: {e}")

def run():
    logger.info("Starting rug_pull_monitor heartbeat recovery diagnostic")
    
    health_rows = check_heartbeat_status()
    age_hours = get_last_heartbeat_age_hours(health_rows) if health_rows else None
    
    pid_info = check_pid_file()
    pid_exists = pid_info.get('exists', False)
    pid_value = pid_info.get('pid')
    process_alive = check_process_running(pid_value) if pid_value else False
    
    supervisord_markers = check_supervisord_status()
    source_analysis = read_source_for_heartbeat_issues()
    
    logger.info(f"Heartbeat age: {age_hours} hours (threshold: {HEARTBEAT_THRESHOLD_HOURS}h)")
    logger.info(f"PID file exists: {pid_exists}, PID: {pid_value}, Process alive: {process_alive}")
    logger.info(f"Supervisord markers found: {supervisord_markers}")
    logger.info(f"Source analysis: {source_analysis}")
    
    if age_hours is None:
        diagnosis = "no_heartbeat_recorded"
        details = "No service_health records found for rug_pull_monitor"
    elif age_hours > HEARTBEAT_THRESHOLD_HOURS:
        if not pid_exists:
            diagnosis = "stale_heartbeat_pid_missing"
            details = f"Heartbeat age {age_hours:.1f}h exceeds {HEARTBEAT_THRESHOLD_HOURS}h threshold, PID file missing"
        elif not process_alive:
            diagnosis = "stale_heartbeat_process_dead"
            details = f"Heartbeat age {age_hours:.1f}h exceeds threshold, PID exists but process not running"
        else:
            diagnosis = "stale_heartbeat_process_alive"
            details = f"Heartbeat age {age_hours:.1f}h exceeds threshold but process appears alive - may be loop blocked"
    else:
        diagnosis = "heartbeat_healthy"
        details = f"Heartbeat age {age_hours:.1f}h within threshold"
    
    if source_analysis.get('issues'):
        diagnosis = diagnosis + "_source_issues"
        details = details + f" | Source issues: {source_analysis['issues']}"
    
    logger.info(f"Diagnosis: {diagnosis}")
    logger.info(f"Details: {details}")
    
    write_diagnostic_result(diagnosis, details)
    
    logger.info("Diagnostic complete")
    sys.exit(0)

if __name__ == '__main__':
    run()