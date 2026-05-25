import logging
import os
import time
import threading
import requests
import sys
from datetime import datetime, timezone

# Ensure mcp_fingerprinter (sibling module) is importable
_SENTINEL_DIR = '/home/workspace/zo_sentinel'
if _SENTINEL_DIR not in sys.path:
    sys.path.insert(0, _SENTINEL_DIR)

WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'
SERVICE_NAME = 'fingerprint_runner_daemon_v3'
PID_FILE = '/home/workspace/logs/fingerprint_runner_daemon_v3.lock'
LOG_FILE = '/home/workspace/logs/fingerprint_runner_daemon_v3.log'
CYCLE_INTERVAL = 600
HEARTBEAT_INTERVAL = 30

os.makedirs('/home/workspace/logs', exist_ok=True)

logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(logging.INFO)
fh = logging.FileHandler(LOG_FILE)
fh.setLevel(logging.INFO)
fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logger.addHandler(fh)

import mcp_fingerprinter

# Patch (2026-04-27): the actual function in mcp_fingerprinter is
# generate_fingerprint(server_id), not fingerprint(). It also writes to
# mcp_fingerprints itself, so the daemon must NOT re-write.
_FP_FN = getattr(mcp_fingerprinter, 'generate_fingerprint', None) \
         or getattr(mcp_fingerprinter, 'fingerprint', None)


def check_single_instance():
    pid = os.getpid()
    try:
        with open(PID_FILE, 'r') as f:
            existing_pid = int(f.read().strip())
        if existing_pid != pid:
            try:
                os.kill(existing_pid, 0)
                logger.warning(f'Another instance running as PID {existing_pid}, exiting')
                return False
            except ProcessLookupError:
                pass  # stale lockfile
    except (FileNotFoundError, ValueError):
        pass
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    return True


def send_heartbeat():
    try:
        ts = datetime.now(timezone.utc).isoformat()
        payload = {'table': 'service_health',
                   'rows': {'service': SERVICE_NAME, 'last_heartbeat': ts},
                   'wait': True}
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
    except Exception as e:
        logger.warning(f'Heartbeat failed: {e}')


def ws_query(sql, timeout=10):
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={'sql': sql}, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f'Query failed: {e}')
        return None


def heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def cycle():
    if _FP_FN is None:
        logger.error('mcp_fingerprinter exposes neither generate_fingerprint() nor fingerprint() -- abort cycle')
        return

    sql = (
        "SELECT server_id FROM mcp_server_registry r "
        "WHERE NOT EXISTS (SELECT 1 FROM mcp_fingerprints f WHERE f.server_id = r.server_id) "
        "LIMIT 30"
    )
    result = ws_query(sql)
    if result is None:
        logger.warning('No query result, skipping cycle')
        return

    rows = result.get('rows', [])
    if not rows:
        logger.info('cycle done: targets=0 fingerprinted=0 errors=0 timeouts=0')
        return

    targets = len(rows)
    fingerprinted = 0
    errors = 0
    timeouts = 0

    for rec in rows:
        server_id = rec.get('server_id') if isinstance(rec, dict) else None
        if not server_id:
            continue
        start = time.time()
        try:
            # generate_fingerprint() writes to mcp_fingerprints itself.
            # Returns the fingerprint dict on success, None on failure.
            fp = _FP_FN(server_id)
            elapsed = time.time() - start
            if elapsed > 5.0:
                logger.warning(f'server_id={server_id} fingerprinter took {elapsed:.1f}s (budget=5s)')
                timeouts += 1
            if fp is not None:
                fingerprinted += 1
            else:
                errors += 1
        except Exception as e:
            logger.warning(f'fingerprint error for server_id={server_id}: {e}')
            errors += 1

    logger.info(f'cycle done: targets={targets} fingerprinted={fingerprinted} errors={errors} timeouts={timeouts}')


def run():
    if not check_single_instance():
        return
    hb = threading.Thread(target=heartbeat_loop, daemon=True)
    hb.start()
    fn_name = _FP_FN.__name__ if _FP_FN else '<none>'
    logger.info(f'{SERVICE_NAME} starting, PID={os.getpid()}, fingerprint_fn={fn_name}')
    while True:
        try:
            cycle()
        except Exception as e:
            logger.error(f'cycle exception: {e}')
        time.sleep(CYCLE_INTERVAL)


if __name__ == '__main__':
    run()