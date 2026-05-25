import os
import sys
import time
import json
import signal
import logging
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_DIR = Path('/home/workspace/zo_sentinel')
LOG_DIR = Path('/home/workspace/logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / 'supply_chain_enrichment_wiring.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(str(LOG_FILE))]
)
logger = logging.getLogger('supply_chain_enrichment_wiring')

SERVICE_NAME = 'supply_chain_enrichment_wiring'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772'
EXECUTE_SERVICE_URL = 'http://localhost:8772'
POLL_SECS = 300
MIN_DISTINCT_SCORES = 20
MIN_FINGERPRINTS = 34


def ws_query(sql: str) -> list:
    resp = requests.post(
        QUERY_SERVICE_URL + '/query',
        json={'sql': sql},
        timeout=30
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])


def ws_write(table: str, rows: list):
    resp = requests.post(
        WRITE_SERVICE_URL + '/write',
        json={'table': table, 'rows': rows},
        timeout=30
    )
    resp.raise_for_status()


def ws_execute(sql: str):
    resp = requests.post(
        EXECUTE_SERVICE_URL + '/execute',
        json={'sql': sql},
        timeout=30
    )
    resp.raise_for_status()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        old_pid = int(open(PID_FILE).read().strip())
        try:
            os.kill(old_pid, 0)
            logger.error(f'Another instance is running with PID {old_pid}')
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    logger.info(f'Received signal {signum}, shutting down gracefully')
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    ts = utc_now_iso()
    try:
        ws_write('service_health', [{
            'service': SERVICE_NAME,
            'last_heartbeat': ts,
            'status': 'running',
            'ts': ts
        }])
    except Exception as e:
        logger.warning(f'Heartbeat failed: {e}')


def check_supply_chain_enrichment_table() -> bool:
    try:
        rows = ws_query("""
            SELECT COUNT(*) as cnt FROM information_schema.tables
            WHERE table_name = 'supply_chain_enrichment'
        """)
        return rows and rows[0].get('cnt', 0) > 0
    except Exception as e:
        logger.error(f'Failed to check supply_chain_enrichment table: {e}')
        return False


def check_signal_enrichments_table() -> bool:
    try:
        rows = ws_query("""
            SELECT COUNT(*) as cnt FROM information_schema.tables
            WHERE table_name = 'mcp_signal_enrichments'
        """)
        return rows and rows[0].get('cnt', 0) > 0
    except Exception as e:
        logger.error(f'Failed to check mcp_signal_enrichments table: {e}')
        return False


def get_supply_chain_enrichment_records() -> list:
    try:
        sql = """
            SELECT server_id, score, signal_name, version, max_score,
                   evidence, computed_at
            FROM supply_chain_enrichment
            WHERE score IS NOT NULL
        """
        return ws_query(sql)
    except Exception as e:
        logger.error(f'Failed to fetch supply_chain_enrichment records: {e}')
        return []


def compute_distinct_scores(records: list) -> int:
    if not records:
        return 0
    scores = set()
    for rec in records:
        if rec.get('score') is not None:
            try:
                scores.add(float(rec['score']))
            except (ValueError, TypeError):
                pass
    return len(scores)


def compute_record_count(records: list) -> int:
    return len(records)


def register_signal_source_with_analyser() -> bool:
    try:
        sql = """
            INSERT INTO mcp_signal_sources (source_name, source_type, description,
                                           is_active, registered_at, config)
            VALUES ('supply_chain', 'enrichment',
                    'Supply chain security signal: registry source age, download count, dependencies, publisher verification, stars',
                    true, '{}', '{{"weight": 0.15, "category": "security"}}')
            ON CONFLICT (source_name) DO UPDATE SET
                is_active = true,
                registered_at = excluded.registered_at,
                config = excluded.config
        """.format(utc_now_iso())
        ws_execute(sql)
        logger.info('Registered supply_chain as signal source in mcp_signal_sources')
        return True
    except Exception as e:
        logger.warning(f'Failed to register in mcp_signal_sources (may not exist): {e}')
        return True


def write_to_signal_enrichments(records: list, signal_name: str, version: str) -> int:
    if not records:
        return 0
    written = 0
    for rec in records:
        server_id = rec.get('server_id')
        if not server_id:
            continue
        score = rec.get('score')
        max_score = rec.get('max_score', 1.0)
        evidence = rec.get('evidence', '{}')
        computed_at = rec.get('computed_at', utc_now_iso())
        score_id = hashlib.sha256(
            f'{server_id}:{signal_name}:{computed_at}'.encode()
        ).hexdigest()[:32]
        row = {
            'enrichment_id': score_id,
            'server_id': server_id,
            'signal_type': signal_name,
            'signal_version': version,
            'score': score,
            'max_score': max_score,
            'evidence': evidence,
            'computed_at': computed_at,
            'source': 'supply_chain_enrichment',
            'active': True
        }
        try:
            ws_write('mcp_signal_enrichments', [row])
            written += 1
        except Exception as e:
            logger.error(f'Failed to write enrichment for {server_id}: {e}')
    return written


def check_signal_analyser_v2_wiring() -> bool:
    try:
        result = ws_query("""
            SELECT COUNT(*) as cnt FROM mcp_signal_sources
            WHERE source_name = 'supply_chain'
        """)
        if result and result[0].get('cnt', 0) > 0:
            return True
    except Exception:
        pass
    return False


def verify_signal_analyser_v2_called() -> bool:
    try:
        rows = ws_query("""
            SELECT COUNT(*) as cnt FROM mcp_signal_scores
            WHERE signal_name = 'supply_chain'
        """)
        if rows and rows[0].get('cnt', 0) > 0:
            logger.info('Signal analyser v2 has already processed supply_chain signal')
            return True
    except Exception as e:
        logger.warning(f'Could not check mcp_signal_scores: {e}')
    return False


def call_signal_analyser_v2_register():
    try:
        ws_execute("""
            SELECT signal_analyser_v2__register_signal_source(
                'supply_chain',
                'enrichment',
                'Supply chain security assessment',
                true
            )
        """)
        logger.info('Called signal_analyser_v2 to register supply_chain source')
        return True
    except Exception as e:
        logger.warning(f'signal_analyser_v2 direct call failed (expected if not procedure): {e}')
        return False


def write_wiring_audit(server_id: str, status: str, detail: str):
    try:
        audit_id = hashlib.sha256(
            f'wiring:{server_id}:{status}:{utc_now_iso()}'.encode()
        ).hexdigest()[:32]
        ws_write('audit_log', [{
            'id': audit_id,
            'event_type': 'supply_chain_wiring',
            'target_server_id': server_id,
            'actor': SERVICE_NAME,
            'detail': json.dumps({'status': status, 'detail': detail, 'ts': utc_now_iso()}),
            'created_at': utc_now_iso()
        }])
    except Exception as e:
        logger.warning(f'Failed to write audit log: {e}')


def cycle() -> dict:
    result = {
        'status': 'ok',
        'records_processed': 0,
        'distinct_scores': 0,
        'wiring_active': False,
        'ts': utc_now_iso()
    }
    logger.info('Starting supply_chain_enrichment wiring cycle')

    if not check_signal_enrichments_table():
        logger.warning('mcp_signal_enrichments table not found, creating...')
        try:
            ws_execute("""
                CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
                    enrichment_id VARCHAR,
                    server_id VARCHAR,
                    signal_type VARCHAR,
                    signal_version VARCHAR,
                    score DOUBLE,
                    max_score DOUBLE,
                    evidence JSON,
                    computed_at TIMESTAMPTZ,
                    source VARCHAR,
                    active BOOLEAN,
                    PRIMARY KEY (enrichment_id)
                )
            """)
        except Exception as e:
            logger.error(f'Failed to create mcp_signal_enrichments: {e}')
            return result

    records = get_supply_chain_enrichment_records()
    record_count = compute_record_count(records)
    distinct_scores = compute_distinct_scores(records)

    logger.info(f'Found {record_count} supply_chain records, {distinct_scores} distinct scores')
    result['records_processed'] = record_count
    result['distinct_scores'] = distinct_scores

    if record_count < MIN_FINGERPRINTS:
        logger.warning(f'Insufficient records: {record_count} < {MIN_FINGERPRINTS}, skipping')
        return result

    if distinct_scores < MIN_DISTINCT_SCORES:
        logger.warning(f'Insufficient score diversity: {distinct_scores} < {MIN_DISTINCT_SCORES}, skipping')
        return result

    register_signal_source_with_analyser()
    call_signal_analyser_v2_register()

    signal_name = 'supply_chain'
    version = 'v3'
    written = write_to_signal_enrichments(records, signal_name, version)
    logger.info(f'Wrote {written} records to mcp_signal_enrichments')
    result['records_written'] = written

    for rec in records[:5]:
        write_wiring_audit(rec.get('server_id', 'unknown'), 'enrichment_synced', f'score={rec.get("score")}')

    if verify_signal_analyser_v2_called():
        result['wiring_active'] = True
        logger.info('Supply chain enrichment wired to signal_analyser_v2 - ACTIVE')
    else:
        logger.info('Supply chain enrichment written to enrichments table - waiting for analyser')
        result['wiring_active'] = False

    return result


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    logger.info(f'{SERVICE_NAME} starting')

    while True:
        try:
            result = cycle()
            send_heartbeat()
            logger.info(f'Cycle complete: {result}')
        except Exception as e:
            logger.error(f'Cycle error: {e}')
            send_heartbeat()

        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()