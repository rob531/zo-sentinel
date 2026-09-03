import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/build_server_risk_tier_assignment_router.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('build_server_risk_tier_assignment_router')

PROJECT_DIR = Path('/home/workspace/zo_sentinel')
SERVICE_NAME = 'build_server_risk_tier_assignment_router'
SERVICE_PORT = 8786
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
EXECUTE_SERVICE_URL = 'http://localhost:8772/execute'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
POLL_SECS = 60

app = FastAPI(title=SERVICE_NAME)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: tuple = ()) -> list:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': sql, 'params': list(params)},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        logger.error(f'ws_query failed: {e} | SQL: {sql[:200]}')
        return []


def ws_write(table: str, rows: list) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f'ws_write failed: {e} | table={table}')
        return False


def ws_execute(sql: str, params: tuple = ()) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={'sql': sql, 'params': list(params)},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f'ws_execute failed: {e} | SQL: {sql[:200]}')
        return False


def check_single_instance() -> None:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error(f'Another instance already running with PID {old_pid}. Exiting.')
            sys.exit(1)
        except (OSError, ValueError):
            logger.warning(f'Stale PID file found (PID {old_pid}). Removing.')
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))
    logger.info(f'PID {os.getpid()} written to {PID_FILE}')


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
        logger.info('PID file removed.')
    except Exception as e:
        logger.warning(f'Failed to remove PID file: {e}')


def signal_handler(signum: int, frame) -> None:
    sig_name = signal.Signals(signum).name
    logger.info(f'Received {sig_name}, shutting down gracefully.')
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status: str = 'ok', meta: str = '') -> None:
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': status,
        'meta': meta
    }
    ws_write('service_health', [row])


def ensure_risk_tier_assignment_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_risk_tier_assignment_log (
        assignment_id    VARCHAR PRIMARY KEY,
        server_id         VARCHAR NOT NULL,
        risk_tier         VARCHAR NOT NULL,
        assigned_at       TIMESTAMPTZ NOT NULL,
        trigger           VARCHAR,
        evidence          JSON
    )
    """
    ws_execute(sql)
    logger.info('Table mcp_risk_tier_assignment_log ensured.')


RISK_TIER_THRESHOLDS = {
    'CRITICAL': 0.0,
    'HIGH': 20.0,
    'MEDIUM': 50.0,
    'LOW': 75.0,
    'MINIMAL': 90.0
}


def compute_risk_tier(trust_score: float) -> str:
    if trust_score is None:
        return 'UNKNOWN'
    if trust_score < RISK_TIER_THRESHOLDS['HIGH']:
        return 'CRITICAL'
    if trust_score < RISK_TIER_THRESHOLDS['MEDIUM']:
        return 'HIGH'
    if trust_score < RISK_TIER_THRESHOLDS['LOW']:
        return 'MEDIUM'
    if trust_score < RISK_TIER_THRESHOLDS['MINIMAL']:
        return 'LOW'
    return 'MINIMAL'


def deterministic_id(server_id: str, risk_tier: str, ts: str) -> str:
    import hashlib
    raw = f'{server_id}:{risk_tier}:{ts}'
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def assign_risk_tier_to_server(server_id: str, trigger: str = 'manual') -> dict:
    rows = ws_query(
        "SELECT server_id, name, trust_score, verdict FROM mcp_server_registry WHERE server_id = ?",
        (server_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f'Server {server_id} not found')

    row = rows[0]
    trust_score = row.get('trust_score', 0.0)
    risk_tier = compute_risk_tier(trust_score)
    ts = utc_now_iso()
    assignment_id = deterministic_id(server_id, risk_tier, ts)

    assignment_row = {
        'assignment_id': assignment_id,
        'server_id': server_id,
        'risk_tier': risk_tier,
        'assigned_at': ts,
        'trigger': trigger,
        'evidence': {
            'trust_score': trust_score,
            'verdict': row.get('verdict'),
            'name': row.get('name')
        }
    }
    ws_write('mcp_risk_tier_assignment_log', [assignment_row])

    ws_execute(
        "UPDATE mcp_risk_register SET risk_tier = ?, computed_at = ? WHERE server_id = ?",
        (risk_tier, ts, server_id)
    )

    return {
        'assignment_id': assignment_id,
        'server_id': server_id,
        'risk_tier': risk_tier,
        'trust_score': trust_score,
        'assigned_at': ts,
        'trigger': trigger
    }


def batch_assign_risk_tiers(limit: int = 100) -> dict:
    rows = ws_query(
        """
        SELECT r.server_id, r.trust_score, r.verdict, r.name
        FROM mcp_server_registry r
        LEFT JOIN mcp_risk_tier_assignment_log l
          ON l.server_id = r.server_id
         AND l.assigned_at = (
             SELECT MAX(l2.assigned_at) FROM mcp_risk_tier_assignment_log l2 WHERE l2.server_id = r.server_id
         )
        WHERE l.assignment_id IS NULL
        LIMIT ?
        """,
        (limit,)
    )
    assignments = []
    now = utc_now_iso()
    for row in rows:
        server_id = row['server_id']
        trust_score = row.get('trust_score', 0.0)
        risk_tier = compute_risk_tier(trust_score)
        assignment_id = deterministic_id(server_id, risk_tier, now)

        assignment_row = {
            'assignment_id': assignment_id,
            'server_id': server_id,
            'risk_tier': risk_tier,
            'assigned_at': now,
            'trigger': 'batch',
            'evidence': {
                'trust_score': trust_score,
                'verdict': row.get('verdict'),
                'name': row.get('name')
            }
        }
        assignments.append(assignment_row)
        ws_execute(
            "UPDATE mcp_risk_register SET risk_tier = ?, computed_at = ? WHERE server_id = ?",
            (risk_tier, now, server_id)
        )

    if assignments:
        ws_write('mcp_risk_tier_assignment_log', assignments)

    return {
        'processed': len(assignments),
        'total_found': len(rows),
        'assigned_at': now
    }


def get_risk_tier_history(server_id: str, limit: int = 20) -> list:
    return ws_query(
        """
        SELECT assignment_id, risk_tier, assigned_at, trigger, evidence
        FROM mcp_risk_tier_assignment_log
        WHERE server_id = ?
        ORDER BY assigned_at DESC
        LIMIT ?
        """,
        (server_id, limit)
    )


@app.get('/health')
def health():
    return {'status': 'ok', 'service': SERVICE_NAME, 'uptime': time.time()}


@app.post('/assign/{server_id}')
def assign_server(server_id: str, trigger: str = 'api'):
    result = assign_risk_tier_to_server(server_id, trigger=trigger)
    return result


@app.post('/assign/batch')
def assign_batch(limit: int = Query(default=100, ge=1, le=1000)):
    result = batch_assign_risk_tiers(limit=limit)
    return result


@app.get('/history/{server_id}')
def history(server_id: str, limit: int = Query(default=20, ge=1, le=200)):
    return get_risk_tier_history(server_id, limit=limit)


@app.get('/tier/{server_id}')
def current_tier(server_id: str):
    rows = ws_query(
        "SELECT risk_tier, computed_at FROM mcp_risk_register WHERE server_id = ?",
        (server_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f'No risk tier found for {server_id}')
    return rows[0]


def heartbeat_loop():
    while True:
        try:
            send_heartbeat(status='ok')
        except Exception as e:
            logger.warning(f'Heartbeat failed: {e}')
        time.sleep(POLL_SECS)


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    ensure_risk_tier_assignment_table()
    logger.info(f'{SERVICE_NAME} starting on port {SERVICE_PORT}')
    uvicorn.run(app, host='0.0.0.0', port=SERVICE_PORT, log_level='info')


if __name__ == '__main__':
    run()
    remove_pid_file()