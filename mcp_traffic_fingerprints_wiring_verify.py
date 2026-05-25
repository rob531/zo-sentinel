import logging
import sys
import os
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/mcp_traffic_fingerprints_wiring_verify.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'mcp_traffic_fingerprints_wiring_verify'
SCANNER_MODULE = '/home/workspace/zo_sentinel/mcp_scanner_fingerprints_wiring.py'
FINGERPRINTS_MODULE = '/home/workspace/zo_sentinel/mcp_traffic_fingerprints.py'
FINGERPRINTS_TABLE = 'mcp_fingerprints'


def ws_query(sql, params=None):
    payload = {'table': '__query', 'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])


def ws_write(table, rows):
    if not rows:
        return
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def verify_module_exists(module_path, module_name):
    if not Path(module_path).exists():
        logger.error(f"{module_name} not found at {module_path}")
        return False
    logger.info(f"{module_name} exists at {module_path}")
    return True


def verify_fingerprints_module():
    if not verify_module_exists(FINGERPRINTS_MODULE, 'mcp_traffic_fingerprints'):
        return False
    
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("mcp_traffic_fingerprints", FINGERPRINTS_MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        if not hasattr(module, 'detect_mcp_methods'):
            logger.error("mcp_traffic_fingerprints missing detect_mcp_methods function")
            return False
        
        logger.info("detect_mcp_methods function found in mcp_traffic_fingerprints")
        return True
    except Exception as e:
        logger.error(f"Failed to import mcp_traffic_fingerprints: {e}")
        return False


def verify_scanner_module():
    if not verify_module_exists(SCANNER_MODULE, 'mcp_scanner_fingerprints_wiring'):
        return False
    
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("mcp_scanner_fingerprints_wiring", SCANNER_MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        logger.info("mcp_scanner_fingerprints_wiring imported successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to import mcp_scanner_fingerprints_wiring: {e}")
        return False


def verify_fingerprints_table_schema():
    try:
        cols = ws_query(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            [FINGERPRINTS_TABLE]
        )
        col_names = [c['column_name'] for c in cols]
        
        required_cols = ['fingerprint_id', 'target_server_id', 'method_name', 'detected_at']
        for col in required_cols:
            if col not in col_names:
                logger.error(f"Table {FINGERPRINTS_TABLE} missing required column: {col}")
                return False
        
        logger.info(f"Table {FINGERPRINTS_TABLE} schema verified: {col_names}")
        return True
    except Exception as e:
        logger.error(f"Failed to verify table schema: {e}")
        return False


def create_test_probe_server():
    mock_probe_response = {
        'jsonrpc': '2.0',
        'result': {
            'protocolVersion': '2024-11-05',
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'test-mcp-server', 'version': '1.0.0'}
        },
        'id': 1
    }
    return mock_probe_response


def verify_detect_mcp_methods_wiring():
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("mcp_traffic_fingerprints", FINGERPRINTS_MODULE)
        fingerprints_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fingerprints_mod)
        
        probe_response = create_test_probe_server()
        
        methods = fingerprints_mod.detect_mcp_methods(probe_response)
        
        if not isinstance(methods, list):
            logger.error("detect_mcp_methods should return a list")
            return False
        
        logger.info(f"detect_mcp_methods returned: {methods}")
        return True
    except Exception as e:
        logger.error(f"detect_mcp_methods wiring test failed: {e}")
        return False


def verify_write_to_fingerprints_table():
    test_fingerprint_id = hashlib.sha256(
        f"verify_wiring_{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()[:32]
    
    test_row = {
        'fingerprint_id': test_fingerprint_id,
        'target_server_id': 'verify_test_server',
        'method_name': 'tools/list',
        'detected_at': datetime.now(timezone.utc).isoformat()
    }
    
    try:
        ws_write(FINGERPRINTS_TABLE, test_row)
        logger.info(f"Test write to {FINGERPRINTS_TABLE} succeeded")
        
        result = ws_query(
            f"SELECT * FROM {FINGERPRINTS_TABLE} WHERE fingerprint_id = ?",
            [test_fingerprint_id]
        )
        
        if not result:
            logger.error("Written fingerprint not found in table")
            return False
        
        logger.info(f"Verified write/read round-trip: {result[0]}")
        return True
    except Exception as e:
        logger.error(f"Failed to write/read from {FINGERPRINTS_TABLE}: {e}")
        return False


def verify_scanner_calls_fingerprints():
    try:
        with open(SCANNER_MODULE, 'r') as f:
            scanner_code = f.read()
        
        if 'detect_mcp_methods' not in scanner_code:
            logger.error("mcp_scanner_fingerprints_wiring.py does not call detect_mcp_methods")
            return False
        
        if FINGERPRINTS_TABLE not in scanner_code:
            logger.error(f"mcp_scanner_fingerprints_wiring.py does not reference {FINGERPRINTS_TABLE}")
            return False
        
        logger.info("Scanner module correctly references detect_mcp_methods and mcp_fingerprints table")
        return True
    except Exception as e:
        logger.error(f"Failed to verify scanner wiring: {e}")
        return False


def run_verification():
    logger.info("=" * 60)
    logger.info("Starting MCP Traffic Fingerprints Wiring Verification")
    logger.info("=" * 60)
    
    checks = [
        ("mcp_traffic_fingerprints module exists", verify_fingerprints_module),
        ("mcp_scanner_fingerprints_wiring module exists", verify_scanner_module),
        ("mcp_fingerprints table schema valid", verify_fingerprints_table_schema),
        ("detect_mcp_methods wiring test", verify_detect_mcp_methods_wiring),
        ("Scanner references fingerprints detection", verify_scanner_calls_fingerprints),
        ("Write to fingerprints table round-trip", verify_write_to_fingerprints_table),
    ]
    
    results = []
    for name, check_fn in checks:
        logger.info(f"Running check: {name}")
        try:
            success = check_fn()
            results.append((name, success))
            if success:
                logger.info(f"PASS: {name}")
            else:
                logger.error(f"FAIL: {name}")
        except Exception as e:
            logger.exception(f"EXCEPTION in {name}: {e}")
            results.append((name, False))
    
    logger.info("=" * 60)
    logger.info("Verification Summary")
    logger.info("=" * 60)
    
    all_passed = True
    for name, success in results:
        status = "PASS" if success else "FAIL"
        logger.info(f"  [{status}] {name}")
        if not success:
            all_passed = False
    
    if all_passed:
        logger.info("All wiring checks PASSED")
        return 0
    else:
        logger.error("Some wiring checks FAILED")
        return 1


if __name__ == '__main__':
    sys.exit(run_verification())