import time
import os
import signal
import logging
from datetime import datetime, timedelta

import requests

SERVICE_NAME = 'audit_log_pruner'
SERVICE_PORT = 8792
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'
EXECUTE_SERVICE_URL = 'http://127.0.0.1:8772/execute'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
LOG_FILE = f'/tmp/{SERVICE_NAME}.log'
POLL_SECS = 3600
RETENTION_DAYS = 90
HEARTBEAT_INTERVAL = 60
WRITE_TIMEOUT = 30
MAX_RETRIES = 3
BACKOFF_BASE = 2

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def get_db_path():
    return '/tmp/sentinel.duckdb'


def check_single_instance():
    pid_path = PID_FILE
    if os.path.exists(pid_path):
        with open(pid_path, 'r') as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance is running with PID {old_pid}")
            return False
        except OSError:
            log.warning(f"Stale PID file found, removing...")
            os.remove(pid_path)
    with open(pid_path, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        log.warning(f"Failed to remove PID file: {e}")


def signal_handler(signum, frame):
    sig_name = signal.Signals(signum).name
    log.info(f"Received {sig_name}, shutting down gracefully...")
    remove_pid_file()
    exit(0)


def get_write_url():
    return WRITE_SERVICE_URL


def get_query_url():
    return QUERY_SERVICE_URL


def get_execute_url():
    return EXECUTE_SERVICE_URL


def ws_write(table, rows, retries=MAX_RETRIES):
    url = get_write_url()
    backoff = 1
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.post(url, json={'table': table, 'rows': rows, 'wait': True}, timeout=WRITE_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_error = e
            log.warning(f"Write attempt {attempt + 1}/{retries} failed for {table}: {e}")
            if attempt < retries - 1:
                time.sleep(backoff)
                backoff *= BACKOFF_BASE
    log.error(f"All {retries} write attempts failed: {last_error}")
    return None


def ws_query(sql, retries=MAX_RETRIES):
    url = get_query_url()
    backoff = 1
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.post(url, json={'sql': sql}, timeout=WRITE_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_error = e
            log.warning(f"Query attempt {attempt + 1}/{retries} failed: {e}")
            if attempt < retries - 1:
                time.sleep(backoff)
                backoff *= BACKOFF_BASE
    log.error(f"All {retries} query attempts failed: {last_error}")
    return None


def ws_execute(sql, retries=MAX_RETRIES):
    url = get_execute_url()
    backoff = 1
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.post(url, json={'sql': sql}, timeout=WRITE_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_error = e
            log.warning(f"Execute attempt {attempt + 1}/{retries} failed: {e}")
            if attempt < retries - 1:
                time.sleep(backoff)
                backoff *= BACKOFF_BASE
    log.error(f"All {retries} execute attempts failed: {last_error}")
    return None


def send_heartbeat():
    now = datetime.utcnow().isoformat()
    rows = {'service': SERVICE_NAME, 'last_heartbeat': now}
    result = ws_write('service_health', rows)
    if result:
        log.debug(f"Heartbeat sent: {now}")
    else:
        log.warning(f"Heartbeat failed")
    return result


def ensure_table_exists():
    check_sql = """
    SELECT COUNT(*) as cnt FROM information_schema.tables 
    WHERE table_name = 'audit_log'
    """
    result = ws_query(check_sql)
    if result and result.get('rows') and result['rows'][0]['cnt'] > 0:
        return True
    log.warning("audit_log table does not exist, cannot prune")
    return False


def get_retention_cutoff():
    cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
    return cutoff.strftime('%Y-%m-%d %H:%M:%S')


def count_prunable_records(cutoff_date):
    count_sql = f"""
    SELECT COUNT(*) as cnt FROM audit_log
    WHERE created_at < '{cutoff_date}'
    """
    result = ws_query(count_sql)
    if result and result.get('rows'):
        return result['rows'][0].get('cnt', 0)
    return 0


def delete_old_records(cutoff_date, batch_size=1000):
    deleted_total = 0
    while True:
        delete_sql = f"""
        DELETE FROM audit_log
        WHERE created_at < '{cutoff_date}'
        LIMIT {batch_size}
        """
        result = ws_execute(delete_sql)
        if result and result.get('ok'):
            affected = result.get('affected_rows', 0)
            if affected == 0:
                break
            deleted_total += affected
            log.info(f"Deleted batch of {affected} records, total: {deleted_total}")
            time.sleep(0.1)
        else:
            log.error(f"Delete batch failed: {result}")
            break
    return deleted_total


def prune_audit_log():
    if not ensure_table_exists():
        return 0
    cutoff_date = get_retention_cutoff()
    log.info(f"Retention cutoff: {cutoff_date} ({RETENTION_DAYS} days)")
    prunable_count = count_prunable_records(cutoff_date)
    if prunable_count == 0:
        log.info("No records to prune")
        return 0
    log.info(f"Found {prunable_count} records eligible for pruning")
    deleted = delete_old_records(cutoff_date)
    log.info(f"Pruning complete. Deleted {deleted} records")
    return deleted


def send_startup_heartbeat():
    now = datetime.utcnow().isoformat()
    rows = {'service': SERVICE_NAME, 'last_heartbeat': now}
    for attempt in range(3):
        try:
            resp = requests.post(WRITE_SERVICE_URL, json={'table': 'service_health', 'rows': rows, 'wait': True}, timeout=WRITE_TIMEOUT)
            if resp.status_code == 200:
                log.info(f"Startup heartbeat sent: {now}")
                return True
        except Exception as e:
            log.warning(f"Startup heartbeat attempt {attempt+1} failed: {e}")
            time.sleep(2 ** attempt)
    return False


def run():
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info(f"Starting {SERVICE_NAME}...")
    if not check_single_instance():
        log.error("Cannot start: another instance is running")
        return
    send_startup_heartbeat()
    last_heartbeat_time = time.time()
    log.info(f"{SERVICE_NAME} started successfully. PID: {os.getpid()}")
    log.info(f"Retention policy: {RETENTION_DAYS} days")
    log.info(f"Pruning audit_log table every {POLL_SECS} seconds")
    try:
        while True:
            loop_start = time.time()
            log.info("Starting prune cycle...")
            try:
                deleted = prune_audit_log()
                log.info(f"Cycle complete. Records removed: {deleted}")
            except Exception as e:
                log.error(f"Prune cycle failed: {e}", exc_info=True)
            if time.time() - last_heartbeat_time >= HEARTBEAT_INTERVAL:
                send_heartbeat()
                last_heartbeat_time = time.time()
            elapsed = time.time() - loop_start
            sleep_time = max(1, POLL_SECS - elapsed)
            time.sleep(sleep_time)
    except Exception as e:
        log.error(f"Fatal error in main loop: {e}", exc_info=True)
    finally:
        remove_pid_file()
        log.info(f"{SERVICE_NAME} stopped")


if __name__ == '__main__':
    run()