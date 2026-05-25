#!/usr/bin/env python3
"""
Community Signal Integration Verifier
Verifies community_signal_enrichment.py is properly wired into signal_analyser.
Queries mcp_signal_enrichments WHERE signal_type='community_signal' and 
verifies >=20 distinct scores across the 34 registry fingerprints.
"""
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

# Constants
SERVICE_NAME = 'community_signal_integration_verifier'
WRITE_SERVICE_URL = 'http://localhost:8772'

# Logging
LOG_DIR = '/home/workspace/logs'
LOG_FILE = os.path.join(LOG_DIR, f'{SERVICE_NAME}.log')
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(__name__)


def ws_query(sql: str, params: Optional[tuple] = None) -> List[Dict]:
    """Query DuckDB via write_service."""
    payload = {'sql': sql}
    if params:
        payload['params'] = list(params)
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/query',
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get('rows', [])
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict]) -> bool:
    """Write to DuckDB via write_service."""
    payload = {'table': table, 'rows': rows, 'wait': True}
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/write',
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write failed: {e}")
        return False


def send_heartbeat(status: str, meta: str = ''):
    """Send service heartbeat."""
    now = datetime.now(timezone.utc).isoformat()
    ws_write('service_health', [{
        'service_name': SERVICE_NAME,
        'status': status,
        'last_heartbeat': now,
        'meta': meta
    }])


def check_table_exists(table_name: str) -> bool:
    """Check if a table exists in DuckDB."""
    sql = """
    SELECT table_name FROM information_schema.tables 
    WHERE table_name = ?
    """
    result = ws_query(sql, (table_name,))
    return len(result) > 0


def get_enrichment_cardinality(signal_type: str) -> Tuple[int, List[str]]:
    """
    Get cardinality (distinct fingerprint count) for a signal type.
    Returns (count, list_of_fingerprints)
    """
    sql = """
    SELECT DISTINCT target_server_id, fingerprint 
    FROM mcp_signal_enrichments 
    WHERE signal_type = ?
    """
    result = ws_query(sql, (signal_type,))
    fingerprints = [row.get('fingerprint') or row.get('target_server_id') 
                    for row in result if row.get('fingerprint') or row.get('target_server_id')]
    return len(set(fingerprints)), list(set(fingerprints))


def get_signal_scores_by_fingerprint(signal_type: str) -> Dict[str, float]:
    """
    Get signal scores grouped by fingerprint.
    Returns dict of fingerprint -> list of scores.
    """
    sql = """
    SELECT fingerprint, score_value 
    FROM mcp_signal_enrichments 
    WHERE signal_type = ?
    """
    result = ws_query(sql, (signal_type,))
    scores = {}
    for row in result:
        fp = row.get('fingerprint') or row.get('target_server_id')
        if fp:
            if fp not in scores:
                scores[fp] = []
            scores[fp].append(row.get('score_value'))
    return scores


def get_registry_fingerprint_count() -> int:
    """Get count of fingerprints in mcp_server_registry."""
    sql = "SELECT COUNT(DISTINCT fingerprint) as cnt FROM mcp_server_registry"
    result = ws_query(sql, ())
    if result:
        return result[0].get('cnt', 0)
    return 0


def trace_wiring_path() -> Dict[str, any]:
    """
    Trace the wiring path from signal_analyser to community_signal_enrichment.
    Returns dict with findings about each step in the path.
    """
    findings = {
        'signal_analyser_exists': False,
        'bridge_file_exists': False,
        'wiring_file_exists': False,
        'bridge_function_exists': False,
        'wiring_function_exists': False,
        'community_enrichment_exists': False,
        'issues': []
    }
    
    # Check for signal_analyser.py
    signal_analyser_path = '/home/workspace/zo_sentinel/signal_analyser.py'
    if os.path.exists(signal_analyser_path):
        findings['signal_analyser_exists'] = True
        with open(signal_analyser_path, 'r') as f:
            content = f.read()
            if 'community_signal' in content or 'enrichment' in content:
                logger.info("signal_analyser.py contains enrichment references")
            else:
                findings['issues'].append("signal_analyser.py missing community_signal references")
    
    # Check for bridge file
    bridge_paths = [
        '/home/workspace/zo_sentinel/signal_analyser_enrichment_bridge.py',
        '/home/workspace/zo_mesh/signal_analyser_enrichment_bridge.py'
    ]
    for path in bridge_paths:
        if os.path.exists(path):
            findings['bridge_file_exists'] = True
            with open(path, 'r') as f:
                content = f.read()
                if 'community_signal' in content:
                    findings['bridge_function_exists'] = True
                    logger.info(f"Found bridge at {path}")
            break
    
    # Check for wiring file
    wiring_paths = [
        '/home/workspace/zo_sentinel/community_signal_enrichment_wiring.py',
        '/home/workspace/zo_mesh/community_signal_enrichment_wiring.py'
    ]
    for path in wiring_paths:
        if os.path.exists(path):
            findings['wiring_file_exists'] = True
            with open(path, 'r') as f:
                content = f.read()
                if 'register' in content or 'enrichment' in content:
                    findings['wiring_function_exists'] = True
                    logger.info(f"Found wiring at {path}")
            break
    
    # Check for community_signal_enrichment.py
    enrichment_paths = [
        '/home/workspace/zo_sentinel/community_signal_enrichment.py',
        '/home/workspace/zo_mesh/community_signal_enrichment.py'
    ]
    for path in enrichment_paths:
        if os.path.exists(path):
            findings['community_enrichment_exists'] = True
            logger.info(f"Found community_signal_enrichment at {path}")
            break
    
    return findings


def verify_integration() -> Tuple[bool, str]:
    """
    Main verification logic.
    Returns (success, message)
    """
    logger.info("Starting community signal integration verification")
    
    # Step 1: Check if mcp_signal_enrichments table exists
    if not check_table_exists('mcp_signal_enrichments'):
        return False, "mcp_signal_enrichments table does not exist"
    
    logger.info("mcp_signal_enrichments table exists")
    
    # Step 2: Check registry fingerprint count
    registry_count = get_registry_fingerprint_count()
    logger.info(f"Registry has {registry_count} distinct fingerprints")
    
    if registry_count < 34:
        logger.warning(f"Registry has only {registry_count} fingerprints, expected 34")
    
    # Step 3: Get community_signal enrichment cardinality
    cardinality, fingerprints = get_enrichment_cardinality('community_signal')
    logger.info(f"community_signal cardinality: {cardinality} distinct fingerprints")
    
    # Step 4: Check threshold
    if cardinality >= 20:
        success_msg = f"VERIFICATION PASSED: {cardinality} distinct community_signal scores (>= 20 threshold)"
        logger.info(success_msg)
        
        # Get score distribution
        scores = get_signal_scores_by_fingerprint('community_signal')
        logger.info(f"Scores span {len(scores)} fingerprints")
        
        return True, success_msg
    
    # Step 5: If < 20, trace wiring path
    logger.warning(f"Cardinality {cardinality} < 20, tracing wiring path")
    findings = trace_wiring_path()
    
    issues_str = ", ".join(findings['issues']) if findings['issues'] else "no issues found"
    status_parts = [
        f"VERIFICATION FAILED: only {cardinality} distinct community_signal scores",
        f"expected >= 20 across ~34 registry fingerprints",
        f"wiring findings: bridge={'Y' if findings['bridge_file_exists'] else 'N'},",
        f"wiring={'Y' if findings['wiring_file_exists'] else 'N'},",
        f"enrichment={'Y' if findings['community_enrichment_exists'] else 'N'}",
        f"issues: {issues_str}"
    ]
    
    return False, " ".join(status_parts)


def run():
    """Main entry point."""
    logger.info(f"{SERVICE_NAME} starting")
    
    success, message = verify_integration()
    send_heartbeat('completed', message)
    
    if success:
        logger.info("Verification completed successfully")
        sys.exit(0)
    else:
        logger.error(f"Verification failed: {message}")
        sys.exit(1)


if __name__ == '__main__':
    run()