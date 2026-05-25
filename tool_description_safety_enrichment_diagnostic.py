import logging
import sys
import os
from datetime import datetime, timezone
from typing import Any

WRITE_SERVICE_URL = 'http://localhost:8772'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/tool_description_safety_enrichment_diagnostic.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def ws_write(table: str, rows: list[dict]) -> dict:
    """Write rows to DuckDB via write_service."""
    import requests
    payload = {
        'table': table,
        'rows': rows,
        'wait': True
    }
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str) -> list[dict]:
    """Query DuckDB via write_service."""
    import requests
    payload = {'sql': sql, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat(service_name: str, status: str, meta: dict | None = None) -> None:
    """Send heartbeat to service_health table."""
    now = datetime.now(timezone.utc).isoformat()
    rows = [{
        'service_name': service_name,
        'status': status,
        'ts': now,
        'meta': meta or {}
    }]
    try:
        ws_write('service_health', rows)
    except Exception as e:
        logger.warning('Failed to send heartbeat: %s', e)


def synthetic_metadata() -> dict[str, Any]:
    """Create synthetic tool metadata for testing."""
    return {
        'tool_name': 'test_tool_safety',
        'server_id': 'srv_quarantine_test',
        'tool_description': 'This tool executes arbitrary shell commands on the target system',
        'parameters': [
            {'name': 'command', 'type': 'string', 'required': True, 'description': 'Shell command to execute'}
        ],
        'capabilities': ['execute_code', 'read_files', 'write_files'],
        'risk_level': 'high',
        'auth_required': False,
        'network_access': True
    }


def validate_compute_score_result(result: Any) -> tuple[bool, str]:
    """Validate that compute_score returns expected structure."""
    if result is None:
        return False, 'compute_score returned None'
    
    if not isinstance(result, tuple):
        return False, f'Expected tuple, got {type(result).__name__}'
    
    if len(result) != 2:
        return False, f'Expected tuple of length 2, got length {len(result)}'
    
    score, evidence = result
    
    if not isinstance(score, (int, float)):
        return False, f'Score must be numeric, got {type(score).__name__}'
    
    if not (0.0 <= score <= 100.0):
        return False, f'Score {score} outside valid range [0.0, 100.0]'
    
    if not isinstance(evidence, dict):
        return False, f'Evidence must be dict, got {type(evidence).__name__}'
    
    if not evidence:
        return False, 'Evidence dict is empty'
    
    non_empty_keys = [k for k, v in evidence.items() if v not in (None, '', [], {})]
    if not non_empty_keys:
        return False, f'Evidence dict has no non-empty values: {evidence}'
    
    return True, 'Validation passed'


def run_diagnostic() -> bool:
    """Run the full diagnostic suite."""
    logger.info('Starting tool_description_safety_enrichment diagnostic')
    logger.info('Importing tool_description_safety_enrichment module')
    
    try:
        from tool_description_safety_enrichment import compute_score
        logger.info('Successfully imported compute_score function')
    except ImportError as e:
        logger.error('Failed to import compute_score: %s', e)
        return False
    
    test_metadata = synthetic_metadata()
    logger.info('Testing compute_score with synthetic metadata: %s', test_metadata)
    
    try:
        result = compute_score(test_metadata)
        logger.info('compute_score returned: %s', result)
    except Exception as e:
        logger.error('compute_score raised exception: %s', e)
        send_heartbeat('tool_description_safety_enrichment_diagnostic', 'failed', {'error': str(e)})
        return False
    
    is_valid, message = validate_compute_score_result(result)
    logger.info('Validation result: %s - %s', 'PASS' if is_valid else 'FAIL', message)
    
    if not is_valid:
        send_heartbeat('tool_description_safety_enrichment_diagnostic', 'quarantine_confirmed', {
            'validation_failed': message,
            'result_type': type(result).__name__,
            'result': str(result)[:500]
        })
        return False
    
    score, evidence = result
    logger.info('Score: %s (type: %s)', score, type(score).__name__)
    logger.info('Evidence keys: %s', list(evidence.keys()))
    
    logger.info('Diagnostic PASSED - tool_description_safety_enrichment is safe to unquarantine')
    send_heartbeat('tool_description_safety_enrichment_diagnostic', 'passed', {
        'score': float(score),
        'evidence_keys': list(evidence.keys())
    })
    
    return True


if __name__ == '__main__':
    success = run_diagnostic()
    if success:
        logger.info('Diagnostic completed successfully')
        sys.exit(0)
    else:
        logger.error('Diagnostic failed')
        sys.exit(1)