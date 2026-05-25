import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SERVICE_NAME = 'daemon_diagnostic_probe'
WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_PORT = 8794
PID_FILE = '/home/workspace/zo_sentinel/.daemon_diagnostic_probe.pid'
LOG_FILE = '/home/workspace/logs/daemon_diagnostic_probe.log'
STALENESS_THRESHOLD_MINUTES = 60

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def check_single_instance():
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        old_pid = pid_path.read_text().strip()
        if old_pid and os.path.exists(f'/proc/{old_pid}'):
            logger.error(f"Instance already running with PID {old_pid}")
            sys.exit(1)
        else:
            pid_path.unlink()
    pid_path.write_text(str(os.getpid()))


def remove_pid_file():
    Path(PID_FILE).unlink(missing_ok=True)


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def ws_query(sql):
    payload = {
        'sql': sql,
        'wait': True
    }
    resp = requests.post(f'{WRITE_SERVICE_URL}/query', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get('rows', [])


def ws_write(table, rows):
    payload = {
        'table': table,
        'rows': rows,
        'wait': True
    }
    resp = requests.post(f'{WRITE_SERVICE_URL}/write', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat(status='running', meta=None):
    ts = datetime.now(timezone.utc).isoformat()
    heartbeat_row = {
        'service_name': SERVICE_NAME,
        'status': status,
        'ts': ts,
        'meta': meta or {}
    }
    ws_write('service_health', heartbeat_row)


def get_staleness_minutes(last_heartbeat_str):
    try:
        if last_heartbeat_str.endswith('Z'):
            last_heartbeat_str = last_heartbeat_str[:-1] + '+00:00'
        last_ts = datetime.fromisoformat(last_heartbeat_str)
        now = datetime.now(timezone.utc)
        delta = now - last_ts
        return delta.total_seconds() / 60
    except (ValueError, TypeError) as e:
        logger.warning(f"Could not parse timestamp '{last_heartbeat_str}': {e}")
        return None


def format_duration(minutes):
    if minutes is None:
        return "unknown"
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    if hours > 0:
        return f"{hours}h{mins:02d}m"
    return f"{mins}m"


def diagnose_stale_daemons():
    logger.info("Starting daemon diagnostic probe cycle")
    
    sql = """
    SELECT service_name, status, last_heartbeat
    FROM service_health
    WHERE last_heartbeat IS NOT NULL
    ORDER BY service_name
    """
    
    try:
        rows = ws_query(sql)
    except Exception as e:
        logger.error(f"Failed to query service_health: {e}")
        return
    
    if not rows:
        logger.warning("No service health records found")
        return
    
    stale_count = 0
    healthy_count = 0
    diagnostic_entries = []
    now_ts = datetime.now(timezone.utc).isoformat()
    
    for row in rows:
        service_name = row.get('service_name', 'unknown')
        status = row.get('status', 'unknown')
        last_heartbeat = row.get('last_heartbeat')
        
        if last_heartbeat:
            staleness = get_staleness_minutes(last_heartbeat)
            is_stale = staleness is not None and staleness > STALENESS_THRESHOLD_MINUTES
            
            if is_stale:
                stale_count += 1
                duration_str = format_duration(staleness)
                logger.warning(
                    f"STALE DAEMON DETECTED: {service_name} | "
                    f"Last heartbeat: {last_heartbeat} | "
                    f"Stale for: {duration_str} | "
                    f"Status: {status}"
                )
                diagnostic_entries.append({
                    'service_name': service_name,
                    'staleness_minutes': round(staleness, 2) if staleness else None,
                    'last_heartbeat': last_heartbeat,
                    'current_status': status,
                    'diagnostic_ts': now_ts,
                    'severity': 'high' if staleness and staleness > 240 else 'medium'
                })
            else:
                healthy_count += 1
                logger.debug(f"Healthy: {service_name} | Last: {last_heartbeat}")
        else:
            logger.warning(f"DAEMON WITH NULL HEARTBEAT: {service_name} | Status: {status}")
            diagnostic_entries.append({
                'service_name': service_name,
                'staleness_minutes': None,
                'last_heartbeat': None,
                'current_status': status,
                'diagnostic_ts': now_ts,
                'severity': 'critical'
            })
    
    if diagnostic_entries:
        logger.info(
            f"Diagnostic summary: {stale_count} stale, {healthy_count} healthy daemons"
        )
        for entry in diagnostic_entries:
            severity = entry.get('severity', 'unknown')
            svc = entry.get('service_name')
            stale_min = entry.get('staleness_minutes')
            if stale_min is not None:
                logger.info(
                    f"  [{severity.upper()}] {svc}: {format_duration(stale_min)} stale"
                )
            else:
                logger.info(f"  [CRITICAL] {svc}: NULL heartbeat")
    
    if stale_count == 0 and healthy_count > 0:
        logger.info(f"All {healthy_count} daemons appear healthy")


def cycle():
    diagnose_stale_daemons()


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info(f"{SERVICE_NAME} starting up | PID: {os.getpid()}")
    logger.info(f"Staleness threshold: {STALENESS_THRESHOLD_MINUTES} minutes")
    
    POLL_SECS = 300
    
    while True:
        try:
            cycle()
            send_heartbeat(status='running', meta={'poll_secs': POLL_SECS})
        except Exception as e:
            logger.exception(f"Error in cycle: {e}")
            try:
                send_heartbeat(status='error', meta={'error': str(e)})
            except Exception:
                pass
        
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    try:
        run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        remove_pid_file()
        sys.exit(0)