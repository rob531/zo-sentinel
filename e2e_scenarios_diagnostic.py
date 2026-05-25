import sys
sys.path.insert(0, '/home/workspace/zo_sentinel')

import json
import traceback
import requests
from datetime import datetime

DIAG_LOG = []

def log(msg):
    ts = datetime.utcnow().isoformat()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    DIAG_LOG.append(line)

def read_scenario_file():
    with open('/home/workspace/zo_sentinel/e2e_scenarios.py', 'r') as f:
        return f.read()

def find_cohort_7_n5():
    content = read_scenario_file()
    lines = content.split('\n')
    in_cohort_7 = False
    cohort_7_lines = []
    n5_lines = []
    in_n5 = False
    indent_level = 0

    for i, line in enumerate(lines):
        if 'cohort_7' in line.lower() or 'Cohort7' in line or 'Cohort 7' in line:
            in_cohort_7 = True
        if in_cohort_7 and ('def test_' in line or 'async def test_' in line):
            if '_n5' in line.lower() or 'n5' in line.lower():
                in_n5 = True
                n5_start = i
            else:
                in_n5 = False
        if in_n5:
            n5_lines.append(f"L{i+1}: {line}")

    log(f"=== COHORT 7 N5 SOURCE ===")
    for l in n5_lines[:60]:
        log(l)

    return n5_lines

def check_cohort_7_n5_structure():
    content = read_scenario_file()
    lines = content.split('\n')

    in_n5 = False
    n5_body = []
    n5_start = 0

    for i, line in enumerate(lines):
        if 'def test_cohort_7_n5' in line or 'async def test_cohort_7_n5' in line:
            in_n5 = True
            n5_start = i
        elif in_n5 and line.strip().startswith('def '):
            break
        elif in_n5:
            n5_body.append((i, line))

    log(f"=== COHORT_7_N5 TEST BODY ({len(n5_body)} lines) ===")
    for lineno, line in n5_body:
        log(f"L{lineno+1}: {line}")

    return n5_body

def parse_assertions(n5_body):
    log("=== ASSERTION ANALYSIS ===")
    assertions = []
    for lineno, line in n5_body:
        stripped = line.strip()
        if any(kw in stripped for kw in ['assert', 'raise', '.get(', '.get (', 'response.json', 'resp.json']):
            assertions.append((lineno+1, stripped))
            log(f"L{lineno+1}: {stripped}")
    return assertions

def probe_registry_state():
    log("=== PROBING REGISTRY STATE ===")
    try:
        resp = requests.post('http://127.0.0.1:8772/query', json={
            'sql': "SELECT server_id, name, verdict, trust_score, scan_count FROM mcp_server_registry ORDER BY scan_count DESC LIMIT 20"
        }, timeout=10)
        data = resp.json()
        log(f"Registry rows returned: {data.get('count', len(data.get('rows', [])))}")
        for row in data.get('rows', [])[:5]:
            log(f"  {row}")
    except Exception as e:
        log(f"Registry probe failed: {e}")

    try:
        resp = requests.post('http://127.0.0.1:8772/query', json={
            'sql': "SELECT * FROM mcp_signal_scores ORDER BY scored_at DESC LIMIT 10"
        }, timeout=10)
        data = resp.json()
        log(f"Signal scores count: {data.get('count', len(data.get('rows', [])))}")
    except Exception as e:
        log(f"Signal scores probe failed: {e}")

def check_service_dependencies():
    log("=== SERVICE DEPENDENCY CHECK ===")
    services = [
        ('8772', 'write_service'),
        ('8773', 'inference_router'),
        ('8775', 'email_guid_auth'),
        ('8776', 'manual_override_api'),
        ('8777', 'advanced_filter_api'),
        ('8779', 'forensic_detail_api'),
        ('8780', 'approval_workflow'),
        ('8781', 'registry_api'),
        ('8782', 'search_api'),
        ('8784', 'nl_query_engine'),
        ('8785', 'rule_engine_api'),
        ('8790', 'ui_server'),
    ]
    for port, name in services:
        try:
            resp = requests.get(f'http://127.0.0.1:{port}/health', timeout=3)
            log(f"  {name} ({port}): {resp.status_code} -> {resp.json()}")
        except requests.exceptions.Timeout:
            log(f"  {name} ({port}): TIMEOUT")
        except Exception as e:
            log(f"  {name} ({port}): DOWN ({e})")

def run_cohort_7_n5_in_isolation():
    log("=== RUNNING COHORT_7_N5 IN ISOLATION ===")
    import subprocess
    result = subprocess.run(
        ['python3', '-c', f'''
import sys
sys.path.insert(0, '/home/workspace/zo_sentinel')
import pytest
sys.exit(pytest.main(['-xvs', '/home/workspace/zo_sentinel/e2e_scenarios.py::test_cohort_7_n5', '--tb=short', '-p', 'no:cacheprovider']))
'''],
        capture_output=True, text=True, timeout=120, cwd='/home/workspace/zo_sentinel'
    )
    log(f"STDOUT (last 80 lines):")
    stdout_lines = result.stdout.split('\n')
    for line in stdout_lines[-80:]:
        if line.strip():
            log(f"  {line}")
    log(f"STDERR (last 40 lines):")
    stderr_lines = result.stderr.split('\n')
    for line in stderr_lines[-40:]:
        if line.strip():
            log(f"  {line}")
    log(f"Return code: {result.returncode}")
    return result.returncode

def find_failure_symptom():
    log("=== FAILURE SYMPTOM ANALYSIS ===")
    content = read_scenario_file()
    lines = content.split('\n')

    in_n5 = False
    n5_lines = []
    for i, line in enumerate(lines):
        if 'def test_cohort_7_n5' in line or 'async def test_cohort_7_n5' in line:
            in_n5 = True
        elif in_n5 and (line.strip().startswith('def ') or line.strip().startswith('async def ')):
            break
        elif in_n5:
            n5_lines.append((i, line))

    for lineno, line in n5_lines:
        stripped = line.strip()
        if stripped.startswith('assert'):
            log(f"ASSERTION at L{lineno+1}: {stripped}")
        if 'time.sleep' in stripped or 'asyncio.sleep' in stripped:
            log(f"SLEEP at L{lineno+1}: {stripped}")
        if '.get(' in stripped or '.json()' in stripped:
            log(f"HTTP GET at L{lineno+1}: {stripped}")
        if 'requests.' in stripped:
            log(f"REQUESTS at L{lineno+1}: {stripped}")

def check_previous_test_artifacts():
    log("=== PREVIOUS ARTIFACT CHECK ===")
    import os
    artifacts = [
        '/tmp/e2e_scenarios.log',
        '/tmp/cohort_7_n5.log',
        '/tmp/pytest.log',
        '/tmp/test_output.log',
        '/home/workspace/zo_sentinel/test_results.json',
    ]
    for path in artifacts:
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    lines = f.readlines()
                    log(f"=== {path} ({len(lines)} lines) ===")
                    for line in lines[-30:]:
                        if line.strip():
                            log(f"  {line.rstrip()}")
            except Exception as e:
                log(f"Cannot read {path}: {e}")

def get_retry_log():
    log("=== RETRY/FAILURE LOG CHECK ===")
    try:
        resp = requests.post('http://127.0.0.1:8772/query', json={
            'sql': "SELECT * FROM audit_log WHERE event_type LIKE '%retry%' OR event_type LIKE '%fail%' ORDER BY created_at DESC LIMIT 20"
        }, timeout=10)
        data = resp.json()
        for row in data.get('rows', []):
            log(f"  {row}")
    except Exception as e:
        log(f"Audit log probe failed: {e}")

def diagnose():
    log("=== ZO-SENTINEL E2E SCENARIOS DIAGNOSTIC ===")
    log(f"Diagnosing cohort_7_n5 failure (retry budget: 2/3)")

    check_service_dependencies()
    probe_registry_state()
    find_cohort_7_n5()
    n5_body = check_cohort_7_n5_structure()
    parse_assertions(n5_body)
    find_failure_symptom()
    check_previous_test_artifacts()
    get_retry_log()

    log("=== ATTEMPTING IN-ISOLATION RUN ===")
    rc = run_cohort_7_n5_in_isolation()

    log("=== DIAGNOSTIC COMPLETE ===")

    findings = {
        'test': 'cohort_7_n5',
        'retry_budget': '2/3',
        'isolation_run_rc': rc,
        'log': DIAG_LOG
    }

    with open('/tmp/e2e_scenarios_diagnostic.jsonl', 'w') as f:
        f.write(json.dumps(findings) + '\n')
    log("Findings written to /tmp/e2e_scenarios_diagnostic.jsonl")

    return findings

if __name__ == '__main__':
    findings = diagnose()
    print("\n=== SUMMARY ===")
    print(f"Test: cohort_7_n5")
    print(f"Isolation run RC: {findings['isolation_run_rc']}")
    print(f"Log entries: {len(findings['log'])}")
    print("Diagnostic complete. See /tmp/e2e_scenarios_diagnostic.jsonl for full output.")