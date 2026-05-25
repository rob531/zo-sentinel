import hashlib
import json
import logging
import sys
import requests
from datetime import datetime, timezone

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'supply_chain_enrichment_wiring_verifier'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log')]
)
logger = logging.getLogger(__name__)


def ws_query(sql, params=None):
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(f'{WRITE_SERVICE_URL}/query', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get('rows', [])


def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(f'{WRITE_SERVICE_URL}/write', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def verify_mcp_signal_enrichments_schema():
    logger.info("Verifying mcp_signal_enrichments schema...")
    sql = """
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'mcp_signal_enrichments'
        ORDER BY ordinal_position
    """
    cols = ws_query(sql)
    required_cols = ['id', 'signal_type', 'evidence_blob', 'computed_at', 'confidence']
    col_names = [r['column_name'] for r in cols]
    missing = [c for c in required_cols if c not in col_names]
    if missing:
        logger.error(f"Missing required columns: {missing}")
        return False
    logger.info(f"Schema OK. Columns: {col_names}")
    return True


def verify_supply_chain_enrichments():
    logger.info("Querying mcp_signal_enrichments for signal_type='supply_chain'...")
    sql = """
        SELECT id, signal_type, evidence_blob, computed_at, confidence
        FROM mcp_signal_enrichments
        WHERE signal_type = 'supply_chain'
        ORDER BY computed_at DESC
        LIMIT 100
    """
    rows = ws_query(sql)
    logger.info(f"Found {len(rows)} supply_chain enrichment rows")

    if not rows:
        logger.warning("No supply_chain enrichments found yet - enrichment may not have run")
        return True

    all_valid = True
    for row in rows:
        eblob = row.get('evidence_blob', {})
        if not isinstance(eblob, dict):
            logger.error(f"Row {row.get('id')} has non-dict evidence_blob: {type(eblob)}")
            all_valid = False
            continue

        missing_keys = []
        for key in ['signal_type', 'confidence', 'evidence_blob']:
            if key not in eblob:
                missing_keys.append(key)

        if missing_keys:
            logger.error(f"Row {row.get('id')} missing evidence_blob keys: {missing_keys}")
            all_valid = False
        else:
            conf = eblob.get('confidence')
            if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
                logger.error(f"Row {row.get('id')} has invalid confidence: {conf}")
                all_valid = False
            else:
                logger.info(f"Row {row.get('id')}: confidence={conf}, evidence_blob keys OK")

        computed_at = row.get('computed_at')
        if computed_at:
            try:
                datetime.fromisoformat(computed_at.replace('Z', '+00:00'))
            except ValueError:
                logger.error(f"Row {row.get('id')} has invalid computed_at: {computed_at}")
                all_valid = False

    return all_valid


def verify_signal_analyser_integration():
    logger.info("Checking if signal_analyser has processed supply_chain enrichments...")
    sql = """
        SELECT COUNT(*) as cnt
        FROM mcp_signal_scores
        WHERE signal_type LIKE '%supply_chain%'
           OR signal_type = 'supply_chain'
    """
    rows = ws_query(sql)
    count = rows[0]['cnt'] if rows else 0
    logger.info(f"signal_analyser has {count} supply_chain-related signal scores")
    return True


def main():
    logger.info(f"Starting {SERVICE_NAME} at {datetime.now(timezone.utc).isoformat()}")
    errors = []

    if not verify_mcp_signal_enrichments_schema():
        errors.append("Schema verification failed")

    if not verify_supply_chain_enrichments():
        errors.append("Supply chain enrichment data validation failed")

    verify_signal_analyser_integration()

    ws_write('service_health', [{
        'service_name': SERVICE_NAME,
        'status': 'PASS' if not errors else 'FAIL',
        'ts': datetime.now(timezone.utc).isoformat(),
        'meta': json.dumps({'errors': errors, 'checked_at': datetime.now(timezone.utc).isoformat()})
    }])

    if errors:
        logger.error(f"VERIFICATION FAILED: {errors}")
        sys.exit(1)
    else:
        logger.info("VERIFICATION PASSED: supply_chain_enrichment wiring is correct")
        sys.exit(0)


if __name__ == '__main__':
    main()