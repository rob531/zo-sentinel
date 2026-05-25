import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    filename='/home/workspace/logs/temporal_stability_enrichment_v2.log'
)
log = logging.getLogger(__name__)

SERVICE_NAME = 'temporal_stability_enrichment_v2'
SERVICE_PORT = None
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
PID_FILE = '/tmp/temporal_stability_enrichment_v2.pid'
POLL_SECS = 300
SIGNAL_NAME = 'temporal_stability'
VERSION = 'v2'
MAX_SCORE = 1.0
MIN_AGE_DAYS = 0
MAX_AGE_DAYS = 1825

HEARTBEAT_INTERVAL = 300

_log_start = 0
_cycle_count = 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        if date_str.endswith('Z'):
            date_str = date_str[:-1] + '+00:00'
        return datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


def compute_days_between(start: str, end: str) -> float:
    start_dt = parse_iso_date(start)
    end_dt = parse_iso_date(end)
    if not start_dt or not end_dt:
        return 0.0
    delta = end_dt - start_dt
    return max(0.0, delta.total_seconds() / 86400.0)


def sigmoid(x: float) -> float:
    if x < -20:
        return 0.0
    if x > 20:
        return 1.0
    import math
    return 1.0 / (1.0 + math.exp(-x))


def log_normalize(x: float) -> float:
    import math
    if x <= 0:
        return 0.0
    return (math.log1p(x)) / 10.0


def softmax_weight(scores: list[float]) -> list[float]:
    import math
    max_score = max(scores) if scores else 0
    exp_scores = [math.exp(s - max_score) for s in scores]
    total = sum(exp_scores)
    if total == 0:
        return [0.0] * len(scores)
    return [e / total for e in exp_scores]


def compute_score(age_days: float, first_seen: str | None, last_seen: str | None) -> float:
    if age_days <= 0:
        return 0.0
    if age_days >= MAX_AGE_DAYS:
        return MAX_SCORE
    normalized_age = age_days / MAX_AGE_DAYS
    score = sigmoid((normalized_age - 0.3) * 8.0)
    score = score * 0.7 + log_normalize(age_days) * 0.3
    return min(MAX_SCORE, max(0.0, score))


def get_score_band(score: float) -> str:
    if score >= 0.85:
        return 'trusted_stable'
    elif score >= 0.65:
        return 'established'
    elif score >= 0.40:
        return 'maturing'
    elif score >= 0.20:
        return 'new_comer'
    else:
        return 'unverified'


def check_single_instance() -> bool:
    pid = str(os.getpid())
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            existing = f.read().strip()
        if existing and existing != pid:
            log.warning('PID file exists: %s (mine=%s)', existing, pid)
            return False
    with open(PID_FILE, 'w') as f:
        f.write(pid)
    return True


def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        log.error('Failed to remove PID file: %s', e)


def signal_handler(signum, frame):
    log.info('Signal %d received, shutting down', signum)
    remove_pid_file()
    sys.exit(0)


def ws_query(sql: str) -> list[dict[str, Any]]:
    try:
        resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        log.error('ws_query failed: %s', e)
        return []


def ws_write(table: str, rows: list[dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL + '/write',
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error('ws_write failed for %s: %s', table, e)
        return False


def send_heartbeat():
    global _log_start, _cycle_count
    try:
        rows = [{
            'service': SERVICE_NAME,
            'status': 'ok',
            'ts': utc_now_iso(),
            'meta': f'cycle={_cycle_count}'
        }]
        requests.post(WRITE_SERVICE_URL + '/write', json={'table': 'service_health', 'rows': rows}, timeout=10)
    except Exception as e:
        log.warning('Heartbeat failed: %s', e)


def get_unscored_servers(limit: int = 100) -> list[dict[str, Any]]:
    sql = f"""
    SELECT 
        r.server_id,
        r.name,
        r.first_seen,
        r.last_seen,
        r.registry_source
    FROM mcp_server_registry r
    WHERE r.first_seen IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM mcp_signal_enrichments e 
        WHERE e.server_id = r.server_id 
        AND e.signal_name = '{SIGNAL_NAME}'
        AND e.version = '{VERSION}'
    )
    LIMIT {limit}
    """
    return ws_query(sql)


def get_servers_needing_rescore(batch_size: int = 50) -> list[dict[str, Any]]:
    sql = f"""
    SELECT 
        r.server_id,
        r.name,
        r.first_seen,
        r.last_seen,
        e.computed_at as last_computed
    FROM mcp_server_registry r
    JOIN mcp_signal_enrichments e ON e.server_id = r.server_id
    WHERE e.signal_name = '{SIGNAL_NAME}'
    AND e.version = '{VERSION}'
    AND e.computed_at < CAST('{utc_now_iso()}' AS TIMESTAMP) - INTERVAL '7 days'
    LIMIT {batch_size}
    """
    return ws_query(sql)


def ensure_table():
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
        signal_id VARCHAR,
        server_id VARCHAR,
        signal_name VARCHAR,
        version VARCHAR,
        score DOUBLE,
        score_band VARCHAR,
        evidence VARCHAR,
        computed_at VARCHAR
    )
    """
    try:
        requests.post(EXECUTE_URL, json={'sql': sql}, timeout=30)
    except Exception as e:
        log.error('ensure_table failed: %s', e)


def compute_enrichment_id(server_id: str) -> str:
    import hashlib
    content = f'{server_id}:{SIGNAL_NAME}:{VERSION}'
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def cycle() -> int:
    global _cycle_count
    _cycle_count += 1
    processed = 0
    ensure_table()
    servers = get_unscored_servers(100)
    if servers:
        log.info('Found %d servers needing temporal stability enrichment', len(servers))
    for server in servers:
        server_id = server.get('server_id')
        first_seen = server.get('first_seen')
        last_seen = server.get('last_seen')
        now = utc_now_iso()
        age_days = compute_days_between(first_seen, now) if first_seen else 0.0
        score = compute_score(age_days, first_seen, last_seen)
        score_band = get_score_band(score)
        evidence = {
            'first_seen': first_seen,
            'last_seen': last_seen,
            'age_days': round(age_days, 2),
            'registry_source': server.get('registry_source')
        }
        signal_id = compute_enrichment_id(server_id)
        rows = [{
            'signal_id': signal_id,
            'server_id': server_id,
            'signal_name': SIGNAL_NAME,
            'version': VERSION,
            'score': round(score, 4),
            'score_band': score_band,
            'evidence': str(evidence),
            'computed_at': now
        }]
        if ws_write('mcp_signal_enrichments', rows):
            processed += 1
            log.debug('Enriched %s: score=%.3f band=%s', server_id, score, score_band)
    if processed > 0:
        log.info('Processed %d temporal stability enrichments', processed)
    return processed


def run():
    global _log_start
    _log_start = time.time()
    log.info('Starting %s', SERVICE_NAME)
    if not check_single_instance():
        log.error('Single instance check failed')
        sys.exit(1)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    last_heartbeat = time.time()
    while True:
        try:
            processed = cycle()
            if processed > 0:
                log.info('Cycle complete: processed=%d uptime=%.1fs', processed, time.time() - _log_start)
            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                send_heartbeat()
                last_heartbeat = time.time()
        except Exception as e:
            log.error('Cycle error: %s', e)
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()