import logging
import sys
import os
import requests
import json
from datetime import datetime, timezone, timedelta

SERVICE_NAME = 'supply_chain_enrichment_integration_check'
WRITE_SERVICE_URL = 'http://localhost:8772'
LOG_FILE = '/home/workspace/logs/signal_analysis.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def ws_query(sql: str, params: tuple = None):
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(
        WRITE_SERVICE_URL + '/query',
        json=payload,
        timeout=15
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get('status') == 'error':
        raise Exception(f"WS query error: {result.get('message')}")
    return result.get('rows', [])


def check_supply_chain_enrichment_rows():
    """Check that supply_chain enrichments exist in mcp_signal_enrichments."""
    sql = """
        SELECT 
            id,
            signal_type,
            registry_source,
            enriched_at,
            evidence_blob
        FROM mcp_signal_enrichments
        WHERE signal_type = 'supply_chain'
        ORDER BY enriched_at DESC
        LIMIT 20
    """
    rows = ws_query(sql)
    return rows


def validate_evidence_blob(evidence_blob_str: str) -> dict:
    """Validate that evidence_blob contains expected supply chain fields."""
    required_fields = [
        'registry_source',
        'age_days',
        'dependency_count'
    ]
    
    optional_fields = [
        'published_date',
        'last_commit',
        'repo_url',
        'license',
        'ecosystem',
        'package_name'
    ]
    
    try:
        blob = json.loads(evidence_blob_str) if isinstance(evidence_blob_str, str) else evidence_blob_str
    except (json.JSONDecodeError, TypeError):
        return {'valid': False, 'reason': 'evidence_blob is not valid JSON'}
    
    missing_required = [f for f in required_fields if f not in blob]
    if missing_required:
        return {'valid': False, 'reason': f'missing required fields: {missing_required}'}
    
    found_optional = [f for f in optional_fields if f in blob]
    
    return {
        'valid': True,
        'found_required': required_fields,
        'found_optional': found_optional
    }


def is_recent_enrichment(enriched_at_str: str, hours_threshold: int = 24) -> bool:
    """Check if enriched_at is within the last N hours."""
    try:
        if enriched_at_str.endswith('Z'):
            enriched_at_str = enriched_at_str[:-1]
        enriched_dt = datetime.fromisoformat(enriched_at_str).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age = now - enriched_dt
        return age.total_seconds() < (hours_threshold * 3600)
    except Exception:
        return False


def check_signal_analyser_integration():
    """Verify signal_analyser is calling supply_chain enrichments."""
    sql = """
        SELECT 
            COUNT(*) as call_count,
            MAX(enriched_at) as last_call
        FROM mcp_signal_enrichments
        WHERE signal_type = 'supply_chain'
    """
    rows = ws_query(sql)
    if rows:
        return {
            'call_count': rows[0].get('call_count', 0),
            'last_call': rows[0].get('last_call')
        }
    return {'call_count': 0, 'last_call': None}


def main():
    logger.info("=== Supply Chain Enrichment Integration Check ===")
    
    all_passed = True
    
    # Check 1: Row count > 0
    logger.info("[CHECK 1] Verifying supply_chain rows exist in mcp_signal_enrichments...")
    try:
        rows = check_supply_chain_enrichment_rows()
        row_count = len(rows)
        logger.info(f"  Found {row_count} supply_chain enrichment rows")
        
        if row_count == 0:
            logger.error("  FAIL: No supply_chain enrichments found. Is supply_chain_enrichment.py running?")
            logger.error("  FAIL: Is signal_analyser or signal_analyser_v2 calling supply_chain_enrichment()?")
            all_passed = False
        else:
            logger.info("  PASS: supply_chain enrichments exist")
    except Exception as e:
        logger.error(f"  FAIL: Could not query mcp_signal_enrichments: {e}")
        all_passed = False
        row_count = 0
    
    # Check 2: evidence_blob contains expected fields
    logger.info("[CHECK 2] Validating evidence_blob structure...")
    valid_blobs = 0
    for row in rows:
        evidence = row.get('evidence_blob', '')
        validation = validate_evidence_blob(evidence)
        if validation.get('valid'):
            valid_blobs += 1
            logger.info(f"  Row {row.get('id')}: valid, fields={validation.get('found_required') + validation.get('found_optional')}")
        else:
            logger.warning(f"  Row {row.get('id')}: INVALID - {validation.get('reason')}")
    
    if row_count > 0:
        validity_ratio = valid_blobs / row_count
        logger.info(f"  Validity ratio: {valid_blobs}/{row_count} = {validity_ratio:.1%}")
        if validity_ratio < 0.5:
            logger.warning("  WARN: Less than 50% of blobs are valid - enricher may have schema issues")
        else:
            logger.info("  PASS: evidence_blob structure is acceptable")
    else:
        logger.warning("  SKIP: No rows to validate")
    
    # Check 3: enriched_at is recent (within 24h)
    logger.info("[CHECK 3] Checking enriched_at timestamps are recent...")
    recent_count = 0
    for row in rows:
        enriched_at = row.get('enriched_at', '')
        if is_recent_enrichment(enriched_at, hours_threshold=24):
            recent_count += 1
        else:
            logger.warning(f"  Row {row.get('id')}: stale enrichment at {enriched_at}")
    
    if row_count > 0:
        recency_ratio = recent_count / row_count
        logger.info(f"  Recency ratio: {recent_count}/{row_count} = {recency_ratio:.1%}")
        if recency_ratio < 0.3:
            logger.warning("  WARN: Less than 30% recent - enricher may not be running")
        else:
            logger.info("  PASS: Enrichments are recent")
    else:
        logger.warning("  SKIP: No rows to check recency")
    
    # Check 4: signal_analyser integration stats
    logger.info("[CHECK 4] Checking signal_analyser integration...")
    try:
        stats = check_signal_analyser_integration()
        logger.info(f"  Total calls: {stats.get('call_count', 0)}")
        logger.info(f"  Last call: {stats.get('last_call', 'never')}")
    except Exception as e:
        logger.warning(f"  Could not get integration stats: {e}")
    
    # Summary
    logger.info("=== Integration Check Summary ===")
    if all_passed and row_count > 0:
        logger.info("RESULT: PASS - supply_chain_enrichment.py is being called by signal_analyser")
        sys.exit(0)
    else:
        logger.error("RESULT: FAIL - supply_chain_enrichment integration has issues")
        sys.exit(1)


if __name__ == '__main__':
    main()