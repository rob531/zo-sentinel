import os
import sys
import logging
import requests
from datetime import datetime, timezone
import hashlib

SERVICE_NAME = 'enrichment_v2_discrimination_audit'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772'
LOG_DIR = '/home/workspace/logs'
LOG_FILE = os.path.join(LOG_DIR, f'{SERVICE_NAME}.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(SERVICE_NAME)

PID_FILE = '/tmp/enrichment_v2_discrimination_audit.pid'

MIN_DISTINCT_SCORES = 10

WEAK_SIGNALS = [
    'permission_scope_enrichment',
    'temporal_stability_enrichment',
    'tool_description_safety_enrichment',
]

ENRICHMENT_V2_MODULES = {
    'permission_scope': 'permission_scope_enrichment_v2',
    'temporal_stability': 'temporal_stability_enrichment_v2',
    'tool_description_safety': 'tool_description_safety_enrichment_v2',
}


def ws_query(sql):
    try:
        resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        log.error(f'ws_query failed: {e}')
        return []


def ws_write(table, rows):
    try:
        resp = requests.post(WRITE_SERVICE_URL + '/write', json={'table': table, 'rows': rows}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f'ws_write failed: {e}')
        return {'ok': False}


def check_single_instance():
    pid = str(os.getpid())
    try:
        with open(PID_FILE, 'r') as f:
            existing = f.read().strip()
        if existing and existing != pid:
            log.warning(f'Another instance running: {existing}. Exiting.')
            sys.exit(0)
    except FileNotFoundError:
        pass
    with open(PID_FILE, 'w') as f:
        f.write(pid)


def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


def signal_handler(signum, frame):
    log.info('Signal received, shutting down.')
    remove_pid_file()
    sys.exit(0)


def ensure_audit_table():
    create_sql = '''
    CREATE TABLE IF NOT EXISTS enrichment_v2_discrimination_audit (
        id INTEGER DEFAULT 0,
        signal_type VARCHAR,
        distinct_scores INTEGER,
        total_rows INTEGER,
        cardinality_ratio REAL,
        status VARCHAR,
        finding VARCHAR,
        module_name VARCHAR,
        audit_ts TIMESTAMPTZ DEFAULT CAST(NOW() AS TIMESTAMPTZ)
    )
    '''
    try:
        resp = requests.post(WRITE_SERVICE_URL + '/execute', json={'sql': create_sql}, timeout=30)
        resp.raise_for_status()
        log.info('Audit table ensured.')
    except Exception as e:
        log.error(f'Failed to create audit table: {e}')


def check_table_exists(table_name):
    sql = f"SELECT COUNT(*) as cnt FROM information_schema.tables WHERE table_name = '{table_name}'"
    rows = ws_query(sql)
    if rows and rows[0].get('cnt', 0) > 0:
        return True
    return False


def query_cardinality(signal_type):
    sql = f'''
    SELECT
        COUNT(DISTINCT score) as distinct_scores,
        COUNT(*) as total_rows,
        signal_type
    FROM mcp_signal_enrichments
    WHERE signal_type = '{signal_type}'
    GROUP BY signal_type
    '''
    rows = ws_query(sql)
    return rows[0] if rows else None


def query_signal_scores_cardinality(signal_type):
    sql = f'''
    SELECT
        COUNT(DISTINCT score) as distinct_scores,
        COUNT(*) as total_rows,
        signal_name
    FROM mcp_signal_scores
    WHERE signal_name = '{signal_type}'
    GROUP BY signal_name
    '''
    rows = ws_query(sql)
    return rows[0] if rows else None


def compute_cardinality_ratio(distinct, total):
    if total == 0:
        return 0.0
    return round(distinct / min(total, 100), 4)


def generate_calibration_directive(signal_type, module_name, distinct, total):
    directive = {
        'directive_id': f'CALIB-{signal_type}-{datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")}',
        'task': f'calibrate_{signal_type}_enrichment',
        'handler': 'zo_sentinel_builder',
        'complexity': 'medium',
        'description': (
            f'Calibration required: {module_name} produces only {distinct} distinct score values '
            f'across {total} processed rows. Target: minimum 10 distinct values for adequate '
            f'signal discrimination. Weak field: score field lacks dynamic range. '
            f'Action: review scoring function, increase score bucketing resolution, '
            f'and validate score_normalization logic to expand score distribution. '
            f'Reference: Appendix B context_efficiency_enrichment deferral condition — '
            f'resolve weak signal discrimination before proceeding with context_efficiency_enrichment integration.'
        ),
        'source': 'enrichment_v2_discrimination_audit',
        'priority': 'high',
        'signal_type': signal_type,
        'module_name': module_name,
        'distinct_scores': distinct,
        'total_rows': total,
        'created_at': datetime.now(timezone.utc).isoformat()
    }
    return directive


def audit_signal(signal_type, module_name):
    log.info(f'Auditing signal_type={signal_type}')

    row = query_cardinality(signal_type)
    if not row:
        log.warning(f'No enrichment data for signal_type={signal_type}')
        record = {
            'signal_type': signal_type,
            'distinct_scores': 0,
            'total_rows': 0,
            'cardinality_ratio': 0.0,
            'status': 'NO_DATA',
            'finding': f'No records found for signal_type={signal_type}',
            'module_name': module_name
        }
        return record

    distinct = row.get('distinct_scores', 0)
    total = row.get('total_rows', 0)
    ratio = compute_cardinality_ratio(distinct, total)

    if distinct < MIN_DISTINCT_SCORES:
        status = 'WEAK_DISCRIMINATION'
        finding = (
            f'Cardinality breach: {distinct} distinct scores < {MIN_DISTINCT_SCORES} minimum. '
            f'Module {module_name} produces insufficient score spread.'
        )
    else:
        status = 'OK'
        finding = f'Cardinality OK: {distinct} distinct scores across {total} rows.'

    record = {
        'signal_type': signal_type,
        'distinct_scores': distinct,
        'total_rows': total,
        'cardinality_ratio': ratio,
        'status': status,
        'finding': finding,
        'module_name': module_name
    }

    log.info(f'  distinct_scores={distinct}, total_rows={total}, status={status}')
    return record


def write_audit_records(records):
    if not records:
        return
    try:
        ws_write('enrichment_v2_discrimination_audit', records)
        log.info(f'Wrote {len(records)} audit records.')
    except Exception as e:
        log.error(f'Failed to write audit records: {e}')


def write_calibration_directive(directive):
    table = 'directives_queue'
    ensure_directives_table_sql = '''
    CREATE TABLE IF NOT EXISTS directives_queue (
        directive_id VARCHAR PRIMARY KEY,
        task VARCHAR,
        handler VARCHAR,
        complexity VARCHAR,
        description VARCHAR,
        source VARCHAR,
        priority VARCHAR,
        status VARCHAR DEFAULT 'pending',
        created_at TIMESTAMPTZ DEFAULT CAST(NOW() AS TIMESTAMPTZ)
    )
    '''
    try:
        resp = requests.post(WRITE_SERVICE_URL + '/execute', json={'sql': ensure_directives_table_sql}, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        log.error(f'Failed to create directives_queue table: {e}')

    rows = [{
        'directive_id': directive['directive_id'],
        'task': directive['task'],
        'handler': directive['handler'],
        'complexity': directive['complexity'],
        'description': directive['description'],
        'source': directive['source'],
        'priority': directive['priority'],
        'status': 'pending'
    }]
    try:
        ws_write('directives_queue', rows)
        log.info(f'Calibration directive written: {directive["directive_id"]}')
    except Exception as e:
        log.error(f'Failed to write calibration directive: {e}')


def send_heartbeat():
    record = {
        'service': SERVICE_NAME,
        'last_heartbeat': datetime.now(timezone.utc).isoformat(),
        'status': 'running',
        'meta': f'Audited {len(WEAK_SIGNALS)} enrichment_v2 signals'
    }
    try:
        requests.post(WRITE_SERVICE_URL + '/write', json={'table': 'service_health', 'rows': [record]}, timeout=10)
    except Exception:
        pass


def run():
    log.info(f'Starting {SERVICE_NAME}')
    check_single_instance()

    try:
        ensure_audit_table()
    except Exception as e:
        log.error(f'Failed to ensure audit table: {e}')

    audit_records = []
    calibration_directives = []

    for signal_type, module_name in ENRICHMENT_V2_MODULES.items():
        record = audit_signal(signal_type, module_name)
        audit_records.append(record)

        if record['status'] == 'WEAK_DISCRIMINATION':
            directive = generate_calibration_directive(
                signal_type, module_name,
                record['distinct_scores'],
                record['total_rows']
            )
            calibration_directives.append(directive)
            write_calibration_directive(directive)

    write_audit_records(audit_records)

    log.info('=== ENRICHMENT V2 DISCRIMINATION REPORT ===')
    for r in audit_records:
        log.info(f"  [{r['status']}] {r['signal_type']}: {r['distinct_scores']} distinct / {r['total_rows']} total — {r['finding']}")

    if calibration_directives:
        log.warning(f'{len(calibration_directives)} calibration directive(s) issued.')
    else:
        log.info('All enrichment_v2 signals pass cardinality threshold.')

    send_heartbeat()
    remove_pid_file()
    log.info(f'{SERVICE_NAME} complete.')
    sys.exit(0)


if __name__ == '__main__':
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    run()