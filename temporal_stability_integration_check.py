import ast
import os
import sys
import logging
import requests
from datetime import datetime, timezone

SERVICE_NAME = 'temporal_stability_integration_check'
WRITE_SERVICE_URL = 'http://localhost:8772'
LOG_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(__name__)

SIGNAL_ANALYSER_PATH = '/home/workspace/zo_sentinel/signal_analyser.py'
TEMPORAL_STABILITY_PATH = '/home/workspace/zo_sentinel/temporal_stability_enrichment.py'


def ws_write(table, rows):
    payload = {
        'table': table,
        'rows': rows if isinstance(rows, list) else [rows],
        'wait': True
    }
    resp = requests.post(WRITE_SERVICE_URL + '/write', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql, params=None):
    payload = {'sql': sql, 'params': params if params else [], 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL + '/query', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat(status, meta=None):
    ts = datetime.now(timezone.utc).isoformat()
    row = {
        'service_name': SERVICE_NAME,
        'status': status,
        'last_heartbeat': ts,
        'meta': meta or {}
    }
    ws_write('service_health', row)


def read_source(path):
    if os.path.exists(path):
        with open(path, 'r') as f:
            return f.read()
    return None


def check_imports_temporal_stability(source_code):
    if not source_code:
        return False, "signal_analyser.py not found"
    tree = ast.parse(source_code)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and 'temporal_stability' in node.module:
                return True, "Found import of temporal_stability module"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if 'temporal_stability' in alias.name:
                    return True, "Found import of temporal_stability module"
    return False, "No import of temporal_stability module found"


def check_calls_compute_score(source_code):
    if not source_code:
        return False, "signal_analyser.py not found"
    tree = ast.parse(source_code)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == 'compute_score':
                    found.append(f"compute_score called on {ast.unparse(node.func.value) if hasattr(ast, 'unparse') else 'object'}")
            elif isinstance(node.func, ast.Name):
                if node.func.id == 'compute_score':
                    found.append(f"compute_score function called")
    if found:
        return True, f"Found {len(found)} compute_score call(s): {', '.join(found[:3])}"
    return False, "No compute_score call found"


def check_writes_mcp_signal_enrichments(source_code):
    if not source_code:
        return False, "signal_analyser.py not found"
    tree = ast.parse(source_code)
    found_signal_type = False
    found_mcp_signal = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                if 'mcp_signal_enrichments' in node.value:
                    found_mcp_signal = True
                if node.value == 'temporal_stability':
                    found_signal_type = True
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword):
            if node.arg == 'signal_type' and isinstance(node.value, ast.Constant):
                if node.value.value == 'temporal_stability':
                    found_signal_type = True
    if found_mcp_signal and found_signal_type:
        return True, "Found write to mcp_signal_enrichments with signal_type='temporal_stability'"
    elif found_mcp_signal:
        return True, "Found write to mcp_signal_enrichments but signal_type='temporal_stability' not confirmed"
    return False, "No write to mcp_signal_enrichments with signal_type='temporal_stability' found"


def check_temporal_stability_module_exists():
    exists = os.path.exists(TEMPORAL_STABILITY_PATH)
    return exists, f"temporal_stability_enrichment.py {'found' if exists else 'NOT found'}"


def audit_to_db(results):
    ts = datetime.now(timezone.utc).isoformat()
    rows = []
    for check_name, passed, detail in results:
        rows.append({
            'check_name': check_name,
            'passed': passed,
            'detail': detail,
            'audited_at': ts
        })
    if rows:
        ws_write('integration_audit_results', rows)


def run():
    logger.info(f"Starting {SERVICE_NAME} audit")
    send_heartbeat('running', {'phase': 'audit_start'})

    results = []
    source_code = read_source(SIGNAL_ANALYSER_PATH)
    temporal_source = read_source(TEMPORAL_STABILITY_PATH)

    check1_passed, check1_detail = check_temporal_stability_module_exists()
    results.append(('temporal_stability_module_exists', check1_passed, check1_detail))
    logger.info(f"Check 1: {check1_detail}")

    check2_passed, check2_detail = check_imports_temporal_stability(source_code)
    results.append(('signal_analyser_imports_temporal_stability', check2_passed, check2_detail))
    logger.info(f"Check 2: {check2_detail}")

    check3_passed, check3_detail = check_calls_compute_score(source_code)
    results.append(('signal_analyser_calls_compute_score', check3_passed, check3_detail))
    logger.info(f"Check 3: {check3_detail}")

    check4_passed, check4_detail = check_writes_mcp_signal_enrichments(source_code)
    results.append(('writes_mcp_signal_enrichments_temporal_stability', check4_passed, check4_detail))
    logger.info(f"Check 4: {check4_detail}")

    audit_to_db(results)

    all_passed = all(r[1] for r in results)
    summary = "PASS" if all_passed else "FAIL"
    logger.info(f"Audit complete: {summary}")
    send_heartbeat('completed' if all_passed else 'failed', {'summary': summary, 'results': results})

    sys.exit(0)


if __name__ == '__main__':
    run()