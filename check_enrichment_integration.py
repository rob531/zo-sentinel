import logging
import sys
import requests
from datetime import datetime, timezone

SERVICE_NAME = 'check_supply_chain_enrichment_integration'
WRITE_SERVICE_URL = 'http://localhost:8772'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def ws_query(sql: str, params: list = None):
    payload = {'table': '__direct_sql__', 'sql': sql, 'params': params or []}
    resp = requests.post(WRITE_SERVICE_URL + '/query', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get('rows', [])


def ws_write(table: str, rows: list):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL + '/write', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def check_enrichment_integration():
    logger.info("Checking supply_chain_enrichment and community_signal_enrichment integration")

    signal_types = ['supply_chain_enrichment', 'community_signal_enrichment']
    results = {}

    for sig_type in signal_types:
        sql = """
            SELECT COUNT(*) as count
            FROM mcp_signal_enrichments
            WHERE signal_type = ?
        """
        rows = ws_query(sql, [sig_type])
        count = rows[0]['count'] if rows else 0
        results[sig_type] = {'count': count}
        logger.info(f"  {sig_type}: {count} rows")

    logger.info("\nSampling evidence_blob shapes:")

    for sig_type in signal_types:
        sql = """
            SELECT evidence_blob
            FROM mcp_signal_enrichments
            WHERE signal_type = ?
            LIMIT 3
        """
        samples = ws_query(sql, [sig_type])
        if samples:
            sample = samples[0].get('evidence_blob', {})
            keys = list(sample.keys()) if isinstance(sample, dict) else 'N/A'
            results[sig_type]['sample_keys'] = keys
            results[sig_type]['sample_shape'] = str(sample)[:500] if sample else 'EMPTY'
            logger.info(f"  {sig_type} sample keys: {keys}")
        else:
            results[sig_type]['sample_keys'] = []
            results[sig_type]['sample_shape'] = 'NO DATA'
            logger.info(f"  {sig_type}: No samples found")

    integration_status = "INTEGRATED" if any(r['count'] > 0 for r in results.values()) else "NOT INTEGRATED - No enrichments found"
    logger.info(f"\nIntegration Status: {integration_status}")

    ws_write('service_health', [{
        'service_name': SERVICE_NAME,
        'status': 'healthy',
        'ts': datetime.now(timezone.utc).isoformat(),
        'meta': str(results)
    }])

    return results


if __name__ == '__main__':
    try:
        check_enrichment_integration()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Check failed: {e}")
        sys.exit(1)