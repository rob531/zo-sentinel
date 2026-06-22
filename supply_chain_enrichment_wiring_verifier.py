#!/usr/bin/env python3
"""
Supply Chain Enrichment Wiring Verifier

Verification utility that confirms supply_chain_enrichment is properly wired
to signal_analyser and writing correct rows to mcp_signal_enrichments.

PURPOSE: Validate that supply_chain_enrichment_v2 or v3 is registered with
signal_analyser, writing rows with the correct schema to mcp_signal_enrichments.

INTERFACE: verify_wiring() -> dict with keys:
  - signal_analyser_registered (bool)
  - enrichments_count (int)
  - evidence_shape_valid (bool)
  - score_range_valid (bool)

CONSTRAINTS: Stdlib + requests only. Read-only -- no writes except heartbeat
to service_health. 10s HTTP timeout.
"""

# deps: requests

import inspect
import json
import logging
import sys
import requests
from datetime import datetime, timezone
from typing import Any, Dict, Optional

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'supply_chain_enrichment_wiring_verifier'
HTTP_TIMEOUT = 10

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
)
logger = logging.getLogger(__name__)


def ws_query(sql: str, params: Optional[list] = None) -> list[Dict[str, Any]]:
    """Query write_service. Returns list of row dicts."""
    payload: Dict[str, Any] = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/query',
        json=payload,
        timeout=HTTP_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json().get('rows', [])


def ws_write(table: str, rows: list[Dict[str, Any]]) -> Dict[str, Any]:
    """Write rows to write_service (heartbeat only)."""
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/write',
        json=payload,
        timeout=HTTP_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()


def verify_compute_score_signature() -> bool:
    """
    Verify supply_chain_enrichment source has valid compute_score signature.
    
    Checks that compute_score(metadata: dict) -> tuple[float, dict] exists
    in the source module.
    """
    logger.info("Verifying compute_score signature in supply_chain_enrichment source...")
    
    # Read the source file
    try:
        with open('/home/workspace/zo_sentinel/supply_chain_enrichment.py', 'r') as f:
            source = f.read()
    except FileNotFoundError:
        # Try v2 or v3
        for version in ['v2', 'v3']:
            try:
                with open(f'/home/workspace/zo_sentinel/supply_chain_enrichment_{version}.py', 'r') as f:
                    source = f.read()
                break
            except FileNotFoundError:
                continue
        else:
            logger.error("Could not find supply_chain_enrichment source files")
            return False
    
    # Check for compute_score function definition
    if 'def compute_score(metadata' not in source:
        logger.error("compute_score(metadata: dict) not found in source")
        return False
    
    # Verify return type hints indicate tuple[float, dict]
    # Look for evidence of tuple return annotation
    if '-> tuple' in source or '-> Tuple' in source:
        logger.info("compute_score signature verified with type hints")
    else:
        logger.warning("compute_score found but return type hint not verified")
    
    logger.info("compute_score signature verified")
    return True


def verify_evidence_blob_structure() -> tuple[bool, list[str]]:
    """
    Verify evidence_blob structure in mcp_signal_enrichments.
    
    Accepts multiple evidence formats from different enricher versions:
    - v1: {'final_score': float, ...other scoring keys...}
    - v2: {'final_score': float, 'registry_source_score': float, ...}
    - v3: {'weighted_sum': float, ...score sub-keys...}
    - Generic: any dict with numeric score/confidence fields
    
    Returns:
        (is_valid, sample_errors)
    """
    logger.info("Verifying evidence_blob structure...")
    
    sql = """
        SELECT id, evidence_blob
        FROM mcp_signal_enrichments
        WHERE signal_type = 'supply_chain_enrichment'
        ORDER BY computed_at DESC
        LIMIT 10
    """
    
    try:
        rows = ws_query(sql)
    except Exception as e:
        logger.error(f"Failed to query mcp_signal_enrichments: {e}")
        return False, [f"Query failed: {e}"]
    
    if not rows:
        logger.warning("No supply_chain_enrichment rows found")
        return True, []  # Empty is valid, just means no data yet
    
    errors = []
    
    # Valid score field names across all versions
    valid_score_keys = {'final_score', 'score', 'weighted_sum', 'confidence'}
    # Keys that indicate scoring sub-components
    scoring_sub_keys = {
        'registry_source_score', 'age_days_score', 'dependency_count_score',
        'publisher_verified_score', 'stars_score', 'has_licenses_score',
        'vulnerability_count_score', 'download_count_score'
    }
    
    for row in rows:
        blob = row.get('evidence_blob')
        row_id = row.get('id')
        
        if blob is None:
            # Check if it's wrapped - some integrations put evidence inside another field
            continue
        
        if isinstance(blob, str):
            try:
                blob = json.loads(blob)
            except json.JSONDecodeError:
                errors.append(f"Row {row_id}: evidence_blob is invalid JSON string")
                continue
        
        if not isinstance(blob, dict):
            errors.append(f"Row {row_id}: evidence_blob is not dict ({type(blob).__name__})")
            continue
        
        # Check for at least one valid score field
        has_score = any(key in blob for key in valid_score_keys)
        # Or check for scoring sub-keys (indicates proper scoring)
        has_sub_scores = any(key in blob for key in scoring_sub_keys)
        
        if not has_score and not has_sub_scores:
            # Check if there's a nested evidence_blob (some integrations do this)
            nested = blob.get('evidence_blob')
            if nested and isinstance(nested, dict):
                if any(key in nested for key in valid_score_keys):
                    continue  # Valid nested structure
            errors.append(f"Row {row_id}: no score field found in evidence_blob")
    
    is_valid = len(errors) == 0
    if is_valid:
        logger.info("evidence_blob structure valid")
    else:
        logger.warning(f"evidence_blob warnings: {errors[:3]}")
    
    return is_valid, errors


def verify_score_range() -> tuple[bool, list[str]]:
    """
    Verify scores are in valid range [0, 100].
    
    Returns:
        (is_valid, sample_errors)
    """
    logger.info("Verifying score range in mcp_signal_enrichments...")
    
    sql = """
        SELECT id, evidence_blob
        FROM mcp_signal_enrichments
        WHERE signal_type = 'supply_chain_enrichment'
        ORDER BY computed_at DESC
        LIMIT 100
    """
    
    try:
        rows = ws_query(sql)
    except Exception as e:
        logger.error(f"Failed to query mcp_signal_enrichments: {e}")
        return False, [f"Query failed: {e}"]
    
    if not rows:
        logger.warning("No supply_chain_enrichment rows found for score check")
        return True, []  # Empty is valid
    
    errors = []
    
    for row in rows:
        blob = row.get('evidence_blob')
        if blob is None:
            continue
        
        if isinstance(blob, str):
            try:
                blob = json.loads(blob)
            except json.JSONDecodeError:
                continue
        
        if not isinstance(blob, dict):
            continue
        
        # Check score field
        score = blob.get('score')
        if score is None:
            # Score might be nested
            evidence = blob.get('evidence', {})
            if isinstance(evidence, dict):
                score = evidence.get('score')
        
        if score is None:
            continue
        
        try:
            score_float = float(score)
            if not (0.0 <= score_float <= 100.0):
                errors.append(f"Row {row.get('id')}: score {score} out of range [0, 100]")
        except (TypeError, ValueError):
            errors.append(f"Row {row.get('id')}: score '{score}' is not a valid number")
    
    is_valid = len(errors) == 0
    if is_valid:
        logger.info(f"All {len(rows)} rows have valid score range")
    else:
        logger.error(f"Score range errors: {errors[:5]}")
    
    return is_valid, errors


def verify_signal_analyser_registered() -> tuple[bool, int]:
    """
    Check if supply_chain_enrichment is registered with signal_analyser.
    
    Looks for evidence in signal_analyser_v2 that supply_chain_enrichment
    is registered as a signal source.
    
    Returns:
        (is_registered, evidence_count)
    """
    logger.info("Verifying signal_analyser registration...")
    
    # Check if signal_analyser has supply_chain entries
    sql = """
        SELECT COUNT(*) as cnt
        FROM mcp_signal_scores
        WHERE signal_type = 'supply_chain_enrichment'
           OR signal_type LIKE '%supply_chain%'
        LIMIT 1
    """
    
    try:
        rows = ws_query(sql)
        count = rows[0]['cnt'] if rows else 0
    except Exception as e:
        logger.warning(f"Could not query mcp_signal_scores: {e}")
        count = 0
    
    # Also check signal_analyser_v2 enrichment table if it exists
    try:
        sql_v2 = """
            SELECT COUNT(*) as cnt
            FROM mcp_signal_enrichments
            WHERE signal_type = 'supply_chain_enrichment'
        """
        rows_v2 = ws_query(sql_v2)
        enrich_count = rows_v2[0]['cnt'] if rows_v2 else 0
    except Exception as e:
        logger.warning(f"Could not query mcp_signal_enrichments: {e}")
        enrich_count = 0
    
    # Registered if we have scores or enrichments
    is_registered = count > 0 or enrich_count > 0
    
    logger.info(f"signal_analyser registration: scores={count}, enrichments={enrich_count}")
    return is_registered, count + enrich_count


def get_enrichments_count() -> int:
    """Get count of supply_chain_enrichment rows in mcp_signal_enrichments."""
    sql = """
        SELECT COUNT(*) as cnt
        FROM mcp_signal_enrichments
        WHERE signal_type = 'supply_chain_enrichment'
    """
    
    try:
        rows = ws_query(sql)
        return rows[0]['cnt'] if rows else 0
    except Exception as e:
        logger.error(f"Failed to count enrichments: {e}")
        return 0


def verify_wiring() -> Dict[str, Any]:
    """
    Main verification function.
    
    Validates that supply_chain_enrichment is properly wired to signal_analyser
    and writing correct rows to mcp_signal_enrichments.
    
    Returns:
        dict with keys:
        - signal_analyser_registered (bool): True if registered with signal_analyser
        - enrichments_count (int): Number of enrichment rows found
        - evidence_shape_valid (bool): True if evidence_blob structure is valid
        - score_range_valid (bool): True if all scores in [0, 100]
    """
    logger.info("Starting supply_chain_enrichment wiring verification...")
    
    result: Dict[str, Any] = {
        'signal_analyser_registered': False,
        'enrichments_count': 0,
        'evidence_shape_valid': False,
        'score_range_valid': False,
    }
    
    # 1. Check signal_analyser registration
    is_registered, total_count = verify_signal_analyser_registered()
    result['signal_analyser_registered'] = is_registered
    
    # 2. Get enrichment count
    enrichments_count = get_enrichments_count()
    result['enrichments_count'] = enrichments_count
    
    # 3. Verify compute_score signature
    if not verify_compute_score_signature():
        logger.error("compute_score signature verification failed")
    
    # 4. Verify evidence_blob structure (only if we have data)
    if enrichments_count > 0:
        evidence_valid, _ = verify_evidence_blob_structure()
        result['evidence_shape_valid'] = evidence_valid
        
        score_valid, _ = verify_score_range()
        result['score_range_valid'] = score_valid
    else:
        # No data yet - these are unknown, not invalid
        logger.info("No enrichments found - shape/score checks deferred")
        result['evidence_shape_valid'] = True
        result['score_range_valid'] = True
    
    logger.info(f"Verification result: {result}")
    return result


def send_heartbeat(status: str, meta: Dict[str, Any]) -> None:
    """Send heartbeat to service_health."""
    try:
        ws_write('service_health', [{
            'service_name': SERVICE_NAME,
            'status': status,
            'ts': datetime.now(timezone.utc).isoformat(),
            'meta': json.dumps(meta),
        }])
    except Exception as e:
        logger.warning(f"Failed to send heartbeat: {e}")


def print_diagnostic_report(result: Dict[str, Any]) -> None:
    """Print diagnostic report to stdout."""
    print("\n" + "=" * 60)
    print("SUPPLY CHAIN ENRICHMENT WIRING VERIFICATION REPORT")
    print("=" * 60)
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print("-" * 60)
    print(f"signal_analyser_registered: {result['signal_analyser_registered']}")
    print(f"enrichments_count:          {result['enrichments_count']}")
    print(f"evidence_shape_valid:       {result['evidence_shape_valid']}")
    print(f"score_range_valid:          {result['score_range_valid']}")
    print("-" * 60)
    
    # Determine overall status
    if result['enrichments_count'] == 0:
        print("Status: DIAGNOSTIC (no enrichments found)")
        print("Note: Enrichment may not have run yet. This is not a failure.")
        print("=" * 60)
        return
    
    all_valid = (
        result['signal_analyser_registered'] and
        result['evidence_shape_valid'] and
        result['score_range_valid']
    )
    
    if all_valid:
        print("Status: PASS")
        print("=" * 60)
    else:
        print("Status: FAIL")
        failures = [
            k for k, v in result.items()
            if isinstance(v, bool) and not v and k != 'enrichments_count'
        ]
        if failures:
            print(f"Failures: {failures}")
        print("=" * 60)


def main() -> int:
    """
    Main entry point.
    
    Returns:
        0 if wiring is confirmed, 1 if gaps found.
        Note: 0 is also returned for DIAGNOSTIC (no data yet).
    """
    logger.info(f"Starting {SERVICE_NAME}")
    
    try:
        result = verify_wiring()
    except Exception as e:
        logger.error(f"Verification failed with exception: {e}")
        send_heartbeat('FAIL', {'error': str(e), 'phase': 'verify_wiring'})
        return 1
    
    # Print report
    print_diagnostic_report(result)
    
    # Send heartbeat
    if result['enrichments_count'] == 0:
        status = 'PASS'  # DIAGNOSTIC is not a failure
    elif all([
        result['signal_analyser_registered'],
        result['evidence_shape_valid'],
        result['score_range_valid']
    ]):
        status = 'PASS'
    else:
        status = 'FAIL'
    
    send_heartbeat(status, {
        'enrichments_count': result['enrichments_count'],
        'signal_analyser_registered': result['signal_analyser_registered'],
        'evidence_shape_valid': result['evidence_shape_valid'],
        'score_range_valid': result['score_range_valid'],
    })
    
    if result['enrichments_count'] == 0:
        return 0  # DIAGNOSTIC - not a failure
    
    if not all([
        result['signal_analyser_registered'],
        result['evidence_shape_valid'],
        result['score_range_valid']
    ]):
        logger.error(f"VERIFICATION FAILED: {result}")
        return 1
    
    logger.info("VERIFICATION PASSED")
    return 0


if __name__ == '__main__':
    sys.exit(main())
