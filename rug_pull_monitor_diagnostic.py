import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'rug_pull_monitor_diagnostic'
SERVICE_PORT = None
PID_FILE = '/home/workspace/zo_sentinel/.rug_pull_monitor_diagnostic.pid'
LOG_DIR = '/home/workspace/logs'
LOG_FILE = os.path.join(LOG_DIR, f'{SERVICE_NAME}.log')

TARGET_SERVICE = 'rug_pull_monitor'
TARGET_PID_FILE = '/home/workspace/zo_sentinel/.rug_pull_monitor.pid'
TARGET_LOG_FILE = os.path.join(LOG_DIR, 'rug_pull_monitor.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(__name__)


def check_single_instance():
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error(f"Already running with PID {old_pid}")
            sys.exit(1)
        except (OSError, ValueError):
            logger.warning(f"Stale PID file {old_pid}, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def ws_query(sql):
    payload = {'sql': sql, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL + '/query', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get('rows', [])


def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL + '/write', json=payload, timeout=30)
    resp.raise_for_status()


def send_heartbeat(status='running', meta=None):
    ts = datetime.now(timezone.utc).isoformat()
    rows = [{
        'service_name': SERVICE_NAME,
        'status': status,
        'last_heartbeat': ts,
        'meta': meta or {}
    }]
    ws_write('service_health', rows)


def check_target_heartbeat():
    sql = f"""
    SELECT service_name, status, last_heartbeat
    FROM service_health
    WHERE service_name = '{TARGET_SERVICE}'
    ORDER BY last_heartbeat DESC
    LIMIT 1
    """
    rows = ws_query(sql)
    if not rows:
        return None, "No heartbeat record found in service_health"
    row = rows[0]
    last_hb = row.get('last_heartbeat')
    if last_hb:
        try:
            hb_dt = datetime.fromisoformat(last_hb.replace('Z', '+00:00'))
            age_seconds = (datetime.now(timezone.utc) - hb_dt).total_seconds()
            age_hours = age_seconds / 3600
            return row, f"Heartbeat age: {age_hours:.1f}h (threshold stale if >1h)"
        except Exception as e:
            return row, f"Could not parse heartbeat: {e}"
    return row, "Heartbeat timestamp missing or null"


def check_target_pid():
    if not os.path.exists(TARGET_PID_FILE):
        return None, f"PID file not found: {TARGET_PID_FILE}"
    with open(TARGET_PID_FILE, 'r') as f:
        pid_str = f.read().strip()
    try:
        pid = int(pid_str)
    except ValueError:
        return None, f"PID file contains non-integer: {pid_str}"
    try:
        os.kill(pid, 0)
        return pid, f"Process alive with PID {pid}"
    except OSError as e:
        return None, f"Process {pid} not running: {e}"


def check_target_log_tail():
    if not os.path.exists(TARGET_LOG_FILE):
        return "Log file not found"
    try:
        with open(TARGET_LOG_FILE, 'r') as f:
            lines = f.readlines()
        if not lines:
            return "Log file empty"
        last_lines = lines[-20:]
        tail = ''.join(last_lines)
        return f"Last 20 lines of {TARGET_LOG_FILE}:\n{tail}"
    except Exception as e:
        return f"Could not read log: {e}"


def check_process_details(pid):
    proc_path = f'/proc/{pid}/cmdline'
    if not os.path.exists(proc_path):
        return "Cannot read /proc"
    try:
        with open(proc_path, 'r') as f:
            cmdline = f.read().replace('\x00', ' ').strip()
        return f"cmdline: {cmdline}"
    except Exception as e:
        return f"Could not read cmdline: {e}"


def cycle():
    logger.info(f"=== Diagnostic cycle started at {datetime.now(timezone.utc).isoformat()} ===")
    findings = {}

    hb_row, hb_msg = check_target_heartbeat()
    findings['heartbeat'] = hb_msg
    if hb_row:
        findings['heartbeat_record'] = hb_row
    logger.info(f"Target heartbeat: {hb_msg}")

    pid, pid_msg = check_target_pid()
    findings['pid_check'] = pid_msg
    logger.info(f"Target PID: {pid_msg}")
    if pid:
        cmdline_info = check_process_details(pid)
        findings['cmdline'] = cmdline_info
        logger.info(cmdline_info)

    log_tail = check_target_log_tail()
    findings['log_tail'] = log_tail
    if 'Last 20 lines' in log_tail:
        logger.info(log_tail)
    else:
        logger.warning(log_tail)

    all_service_health = ws_query(
        "SELECT service_name, status, last_heartbeat FROM service_health ORDER BY last_heartbeat DESC LIMIT 50"
    )
    findings['all_services'] = all_service_health
    logger.info(f"All service health records: {len(all_service_health)} services tracked")

    stale_threshold_hours = 1
    stale_services = []
    for svc in all_service_health:
        ts_str = svc.get('last_heartbeat')
        if ts_str:
            try:
                dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                if age_h > stale_threshold_hours:
                    stale_services.append((svc['service_name'], age_h))
            except Exception:
                pass
    if stale_services:
        logger.warning(f"Stale services detected: {stale_services}")
        findings['stale_services'] = stale_services
    else:
        logger.info("No other stale services detected")

    logger.info(f"=== Diagnostic cycle complete ===")
    return findings


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    logger.info(f"{SERVICE_NAME} starting")
    send_heartbeat(status='running', meta={'target': TARGET_SERVICE})

    POLL_SECS = 300

    while True:
        try:
            findings = cycle()
            send_heartbeat(
                status='running',
                meta={
                    'target': TARGET_SERVICE,
                    'last_findings': {
                        'heartbeat_status': findings.get('heartbeat', 'unknown'),
                        'pid_status': findings.get('pid_check', 'unknown'),
                        'stale_services': findings.get('stale_services', [])
                    }
                }
            )
        except Exception as e:
            logger.exception(f"Error in diagnostic cycle: {e}")
            send_heartbeat(status='error', meta={'error': str(e)})

        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()