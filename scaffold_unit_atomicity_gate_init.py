import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_DIR = Path('/home/workspace/zo_sentinel')
LOG_DIR = PROJECT_DIR / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'unit_atomicity_gate_init.log'),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger('unit_atomicity_gate_init')

SERVICE_NAME = 'unit_atomicity_gate_init'
SERVICE_PORT = 0
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772'
EXECUTE_SERVICE_URL = 'http://localhost:8772'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
POLL_SECS = 300
HEARTBEAT_INTERVAL = 60


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_single_instance() -> None:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error('Another instance is running with PID %s', old_pid)
            sys.exit(1)
        except (OSError, ProcessLookupError):
            logger.warning('Stale PID file found for PID %s, removing', old_pid)
            pid_file.unlink(missing_ok=True)
    pid_file.write_text(str(os.getpid()))


def remove_pid_file() -> None:
    Path(PID_FILE).unlink(missing_ok=True)


def signal_handler(signum: int, frame) -> None:
    signame = signal.Signals(signum).name
    logger.info('Received signal %s (%d), shutting down gracefully', signame, signum)
    remove_pid_file()
    sys.exit(0)


def ws_query(sql: str) -> list:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL + '/query',
            json={'sql': sql},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except requests.RequestException as e:
        logger.error('ws_query failed: %s', e)
        return []


def ws_write(table: str, rows: list) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL + '/write',
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error('ws_write failed: %s', e)
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL + '/execute',
            json={'sql': sql},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error('ws_execute failed: %s', e)
        return False


def send_heartbeat(status: str = 'running', meta: dict = None) -> None:
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': status,
        'meta': meta or {}
    }
    ws_write('service_health', [row])


def ensure_gate_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS unit_atomicity_gate (
        gate_id          VARCHAR PRIMARY KEY,
        gate_name        VARCHAR NOT NULL,
        gate_version     VARCHAR NOT NULL,
        operation_id     VARCHAR,
        table_name       VARCHAR NOT NULL,
        column_name      VARCHAR,
        expected_value   VARCHAR,
        actual_value     VARCHAR,
        gated_at         TIMESTAMPTZ,
        outcome          VARCHAR,
        details_json     JSON
    )
    """
    if ws_execute(sql):
        logger.info('unit_atomicity_gate table ready')
        return True
    logger.error('Failed to create unit_atomicity_gate table')
    return False


def ensure_operation_log_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS unit_atomicity_operation_log (
        operation_id     VARCHAR PRIMARY KEY,
        gate_id          VARCHAR,
        table_name       VARCHAR NOT NULL,
        column_name      VARCHAR,
        before_value     VARCHAR,
        after_value      VARCHAR,
        operation_type   VARCHAR,
        started_at       TIMESTAMPTZ,
        completed_at     TIMESTAMPTZ,
        status           VARCHAR,
        error_detail     VARCHAR
    )
    """
    if ws_execute(sql):
        logger.info('unit_atomicity_operation_log table ready')
        return True
    logger.error('Failed to create unit_atomicity_operation_log table')
    return False


def ensure_violation_log_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS unit_atomicity_violation_log (
        violation_id     VARCHAR PRIMARY KEY,
        gate_id          VARCHAR NOT NULL,
        operation_id     VARCHAR,
        table_name       VARCHAR NOT NULL,
        column_name      VARCHAR,
        expected_value   VARCHAR,
        actual_value     VARCHAR,
        detected_at      TIMESTAMPTZ,
        resolved         BOOLEAN DEFAULT FALSE,
        resolved_at      TIMESTAMPTZ,
        resolution_notes VARCHAR
    )
    """
    if ws_execute(sql):
        logger.info('unit_atomicity_violation_log table ready')
        return True
    logger.error('Failed to create unit_atomicity_violation_log table')
    return False


def get_gate_record(gate_id: str) -> dict | None:
    rows = ws_query(f"SELECT * FROM unit_atomicity_gate WHERE gate_id = '{gate_id}'")
    return rows[0] if rows else None


def insert_initial_gate_record(gate_id: str, gate_name: str, gate_version: str) -> bool:
    row = {
        'gate_id': gate_id,
        'gate_name': gate_name,
        'gate_version': gate_version,
        'gated_at': utc_now_iso(),
        'outcome': 'initialized',
        'details_json': {'initialized_by': SERVICE_NAME, 'init_phase': 'bootstrap'}
    }
    return ws_write('unit_atomicity_gate', [row])


def is_gate_initialized() -> bool:
    rows = ws_query(
        "SELECT COUNT(*) as cnt FROM unit_atomicity_gate WHERE outcome = 'initialized'"
    )
    if rows:
        return int(rows[0].get('cnt', 0)) > 0
    return False


def register_gate_version() -> None:
    rows = ws_query("SELECT gate_id, gate_version FROM unit_atomicity_gate LIMIT 5")
    if not rows:
        insert_initial_gate_record(
            'default-gate-v1',
            'unit_atomicity_default_gate',
            '1.0.0'
        )
        logger.info('Registered default gate version v1.0.0')
    else:
        logger.info('Gate already registered, skipping bootstrap registration')


def cycle() -> None:
    logger.info('Running unit atomicity gate initialization cycle')

    if not ensure_gate_table():
        logger.error('Cannot proceed: gate table unavailable')
        return

    if not ensure_operation_log_table():
        logger.error('Cannot proceed: operation log table unavailable')
        return

    if not ensure_violation_log_table():
        logger.error('Cannot proceed: violation log table unavailable')
        return

    if not is_gate_initialized():
        register_gate_version()
    else:
        logger.info('Gate already initialized, no bootstrap action needed')

    send_heartbeat('running', {'phase': 'cycle_complete', 'ts': utc_now_iso()})


def run() -> None:
    logger.info('Starting %s daemon', SERVICE_NAME)
    check_single_instance()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        resp = requests.get('http://localhost:8772/health', timeout=10)
        if resp.status_code != 200:
            logger.warning('WriteService health check returned %s', resp.status_code)
    except requests.RequestException as e:
        logger.warning('WriteService health check failed: %s', e)

    send_heartbeat('starting', {'init_pid': os.getpid()})

    cycle()

    while True:
        time.sleep(POLL_SECS)
        cycle()


if __name__ == '__main__':
    run()