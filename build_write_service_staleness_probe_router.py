import os
import sys
import signal
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

PROJECT_DIR = Path('/home/workspace/zo_sentinel')
LOG_DIR = PROJECT_DIR / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

SERVICE_NAME = 'write_service_staleness_probe'
SERVICE_PORT = 0
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
EXECUTE_SERVICE_URL = 'http://localhost:8772/execute'
WRITE_SERVICE = 'http://localhost:8772/write'
HEALTH_THRESHOLD_SECONDS = 300
PROBE_INTERVAL_SECONDS = 60

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / f'{SERVICE_NAME}.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

app = FastAPI()

_pid_file_handle: Optional[Any] = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_single_instance() -> bool:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)
            log.error('Another instance already running with PID %d', old_pid)
            return False
        except OSError:
            log.warning('Stale PID file from %d, removing', old_pid)
            pid_file.unlink()
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception as e:
        log.warning('Failed to remove PID file: %s', e)


def signal_handler(signum: int, frame: Any) -> None:
    log.info('Received signal %d, shutting down gracefully', signum)
    remove_pid_file()
    sys.exit(0)


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': sql},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except requests.RequestException as e:
        log.error('ws_query failed for SQL: %s | Error: %s', sql[:200], e)
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE,
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=15
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error('ws_write failed for table %s: %s', table, e)
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={'sql': sql},
            timeout=15
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error('ws_execute failed: %s | Error: %s', sql[:200], e)
        return False


def check_write_service_responsive() -> bool:
    try:
        resp = requests.get(f'{WRITE_SERVICE_URL}/health', timeout=5)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def get_write_service_heartbeat_age() -> Optional[float]:
    rows = ws_query(
        "SELECT last_heartbeat FROM service_health WHERE service = 'write_service' ORDER BY last_heartbeat DESC LIMIT 1"
    )
    if not rows:
        return None
    hb_str = rows[0].get('last_heartbeat')
    if not hb_str:
        return None
    try:
        hb_dt = datetime.fromisoformat(hb_str.replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - hb_dt).total_seconds()
        return age
    except Exception as e:
        log.warning('Failed to parse heartbeat %s: %s', hb_str, e)
        return None


def check_recent_restarts() -> int:
    rows = ws_query(
        "SELECT COUNT(*) as restart_count FROM service_health WHERE service = 'write_service' AND last_heartbeat >= NOW() - INTERVAL '1 hour'"
    )
    if rows and 'restart_count' in rows[0]:
        return int(rows[0]['restart_count'])
    return 0


def compute_staleness_probe() -> Dict[str, Any]:
    responsive = check_write_service_responsive()
    hb_age = get_write_service_heartbeat_age()
    recent_restarts = check_recent_restarts()
    is_stale = hb_age is not None and hb_age > HEALTH_THRESHOLD_SECONDS

    return {
        'responsive': responsive,
        'heartbeat_age_seconds': hb_age,
        'recent_restarts_1h': recent_restarts,
        'is_stale': is_stale,
        'threshold_seconds': HEALTH_THRESHOLD_SECONDS,
        'probed_at': utc_now_iso()
    }


def write_probe_result(result: Dict[str, Any]) -> None:
    ws_execute(
        "CREATE TABLE IF NOT EXISTS write_service_staleness_probe ("
        "  probe_id TEXT, responsive BOOLEAN, heartbeat_age_seconds DOUBLE, "
        "  recent_restarts_1h INTEGER, is_stale BOOLEAN, threshold_seconds INTEGER, "
        "  probed_at TIMESTAMPTZ"
        ")"
    )
    probe_id = f"ws_probe_{utc_now_iso().replace(':', '').replace('-', '').replace('+', '')}"
    hb_age = result.get('heartbeat_age_seconds')
    hb_sql = 'NULL' if hb_age is None else str(hb_age)
    sql = (
        f"INSERT INTO write_service_staleness_probe "
        f"(probe_id, responsive, heartbeat_age_seconds, recent_restarts_1h, is_stale, threshold_seconds, probed_at) "
        f"VALUES ('{probe_id}', {result['responsive']}, {hb_sql}, "
        f"{result['recent_restarts_1h']}, {result['is_stale']}, "
        f"{result['threshold_seconds']}, '{result['probed_at']}')"
    )
    ws_execute(sql)


def send_heartbeat() -> None:
    ws_write('service_health', [{
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': 'ok'
    }])


class ProbeResponse(BaseModel):
    responsive: bool
    heartbeat_age_seconds: Optional[float]
    recent_restarts_1h: int
    is_stale: bool
    threshold_seconds: int
    probed_at: str


@app.get('/health')
def health() -> Dict[str, Any]:
    return {'status': 'ok', 'service': SERVICE_NAME}


@app.get('/probe', response_model=ProbeResponse)
def probe() -> Dict[str, Any]:
    result = compute_staleness_probe()
    write_probe_result(result)
    return result


@app.get('/probe/quick')
def probe_quick() -> Dict[str, Any]:
    return compute_staleness_probe()


def cycle() -> None:
    log.info('Running staleness probe cycle')
    result = compute_staleness_probe()
    write_probe_result(result)
    if result['is_stale']:
        log.warning(
            'WRITE SERVICE STALE: responsive=%s age=%.1fs threshold=%ds restarts_1h=%d',
            result['responsive'],
            result['heartbeat_age_seconds'] or -1,
            result['threshold_seconds'],
            result['recent_restarts_1h']
        )
    else:
        log.info(
            'WRITE SERVICE healthy: responsive=%s age=%.1fs',
            result['responsive'],
            result['heartbeat_age_seconds'] or -1
        )
    send_heartbeat()


def heartbeat_loop() -> None:
    while True:
        try:
            cycle()
        except Exception as e:
            log.exception('Error in probe cycle: %s', e)
        import time
        time.sleep(PROBE_INTERVAL_SECONDS)


def run() -> None:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    if not check_single_instance():
        log.error('Cannot acquire PID file, another instance may be running')
        sys.exit(1)

    log.info('Starting %s on port %d', SERVICE_NAME, SERVICE_PORT)
    try:
        uvicorn.run(app, host='0.0.0.0', port=SERVICE_PORT, log_level='info')
    finally:
        remove_pid_file()


if __name__ == '__main__':
    run()