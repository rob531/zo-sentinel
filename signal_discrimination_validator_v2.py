import logging
import os
import sys
import time
from datetime import datetime, timezone
from hashlib import sha256

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/signal_discrimination_validator_v2.log')]
)
log = logging.getLogger('signal_discrimination_validator_v2')

WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
SERVICE_NAME = 'signal_discrimination_validator_v2'
MIN_DISTINCT_SCORES = 20
MIN_FINGERPRINTS = 34

BREACH_TABLE = 'signal_discrimination_breaches'


def ws_query(sql):
    payload = {'sql': sql}
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])


def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql):
    payload = {'sql': sql}
    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def compute_breach_id(signal_type, run_id):
    content = f"{signal_type}:{run_id}"
    return sha256(content.encode()).hexdigest()


def ensure_breach_table():
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {BREACH_TABLE} (
        breach_id TEXT PRIMARY KEY,
        signal_type TEXT NOT NULL,
        distinct_scores INTEGER NOT NULL,
        fingerprint_count INTEGER NOT NULL,
        threshold_scores INTEGER NOT NULL,
        threshold_fingerprints INTEGER NOT NULL,
        breach_reason TEXT NOT NULL,
        run_id TEXT NOT NULL,
        detected_at TEXT NOT NULL
    )
    """
    ws_execute(ddl)
    log.info("Ensured breach table exists")


def get_signal_type_stats(signal_type):
    distinct_sql = f"""
    SELECT
        COUNT(DISTINCT score) AS distinct_scores,
        COUNT(DISTINCT server_id) AS fingerprint_count
    FROM mcp_signal_enrichments
    WHERE signal_type = '{signal_type}'
    """
    rows = ws_query(distinct_sql)
    if not rows:
        return {'distinct_scores': 0, 'fingerprint_count': 0}
    return rows[0]


def get_all_signal_types():
    sql = """
    SELECT DISTINCT signal_type
    FROM mcp_signal_enrichments
    """
    rows = ws_query(sql)
    return [r['signal_type'] for r in rows]


def detect_breaches(signal_type, stats, run_id):
    breaches = []
    detected_at = utc_now_iso()
    ds = stats['distinct_scores']
    fc = stats['fingerprint_count']

    if ds < MIN_DISTINCT_SCORES:
        reason = f"distinct_scores={ds} < threshold={MIN_DISTINCT_SCORES}"
        breach_id = compute_breach_id(signal_type, run_id)
        breaches.append({
            'breach_id': breach_id,
            'signal_type': signal_type,
            'distinct_scores': ds,
            'fingerprint_count': fc,
            'threshold_scores': MIN_DISTINCT_SCORES,
            'threshold_fingerprints': MIN_FINGERPRINTS,
            'breach_reason': reason,
            'run_id': run_id,
            'detected_at': detected_at
        })
        log.warning(f"BREACH [{signal_type}]: {reason} (fingerprints={fc})")
    else:
        log.info(f"PASS [{signal_type}]: distinct_scores={ds}, fingerprints={fc}")

    return breaches


def write_breaches(breaches):
    if not breaches:
        return
    ws_write(BREACH_TABLE, breaches)
    log.info(f"Wrote {len(breaches)} breach(s) to {BREACH_TABLE}")


def validate_all():
    run_id = sha256(utc_now_iso().encode()).hexdigest()[:16]
    log.info(f"Starting discrimination validation run_id={run_id}")
    ensure_breach_table()

    weak_signals = [
        'permission_scope',
        'temporal_stability',
        'tool_description_safety',
    ]

    all_breaches = []

    for sig in weak_signals:
        stats = get_signal_type_stats(sig)
        breaches = detect_breaches(sig, stats, run_id)
        all_breaches.extend(breaches)

    all_signals = get_all_signal_types()
    for sig in all_signals:
        if sig not in weak_signals:
            stats = get_signal_type_stats(sig)
            ds = stats['distinct_scores']
            fc = stats['fingerprint_count']
            log.info(f"INFO [{sig}]: distinct_scores={ds}, fingerprints={fc}")

    write_breaches(all_breaches)
    return all_breaches


def run():
    log.info(f"{SERVICE_NAME} starting")
    try:
        breaches = validate_all()
        if breaches:
            log.warning(f"Validation complete: {len(breaches)} breach(es) detected")
            sys.exit(1)
        else:
            log.info("Validation complete: all signals pass discrimination floor")
            sys.exit(0)
    except Exception as e:
        log.error(f"Validation failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    run()