#!/usr/bin/env python3
"""
r2_score_supply_chain_threat_assoc_patcher.py
Rewires signal_analyser.py's score_supply_chain to use mcp_threat_associations.
Exit codes: 0=success/idempotent, 1=error, 2=smoke test failed (backup restored), 3=dry-run changes needed
"""
import sys
import os
import re
import json
import time
import shutil
import hashlib

# Service endpoints
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"

SIGNAL_ANALYSER_PATH = "/home/workspace/zo_sentinel/signal_analyser.py"
BACKUP_SUFFIX = ".threat_assoc_backup"
MARKER = "# _zo_threat_assoc_v1"

THREAT_SOURCES = ['alienvault_otx', 'nvd', 'cisa_kev', 'urlhaus', 'abuseipdb']
SEVERITY_SCORES = {'CRITICAL': 0.0, 'HIGH': 15.0, 'MEDIUM': 40.0, 'LOW': 60.0}

DRY_RUN = '--dry-run' in sys.argv


def log(msg):
    print(f"[r2_patcher] {msg}", flush=True)


def ws_query(sql):
    """Execute SELECT query."""
    try:
        import requests
        resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
        if resp.status_code == 200:
            result = resp.json()
            if isinstance(result, dict) and 'rows' in result:
                return result['rows']
            return result if isinstance(result, list) else []
        log(f"Query failed: {resp.status_code} - {resp.text[:200]}")
        return []
    except Exception as e:
        log(f"Query error: {e}")
        return []


def ws_write(table, rows, wait=True):
    """Write to write_service."""
    try:
        import requests
        resp = requests.post(WRITE_SERVICE_URL, json={'table': table, 'rows': rows, 'wait': wait}, timeout=30)
        return resp.status_code == 200
    except Exception as e:
        log(f"Write error: {e}")
        return False


def ws_execute(sql):
    """Execute DDL/DML."""
    try:
        import requests
        resp = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=30)
        return resp.status_code == 200
    except Exception as e:
        log(f"Execute error: {e}")
        return False


def ensure_threat_associations_table():
    """Ensure mcp_threat_associations table exists."""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_threat_associations (
        server_id VARCHAR NOT NULL,
        source VARCHAR NOT NULL,
        threat_type VARCHAR,
        severity VARCHAR,
        evidence TEXT,
        reported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    return ws_execute(sql)


def backup_file(path):
    """Create backup copy."""
    backup_path = path + BACKUP_SUFFIX
    if os.path.exists(backup_path):
        log(f"Backup exists: {backup_path}")
    else:
        shutil.copy2(path, backup_path)
        log(f"Backed up to: {backup_path}")
    return backup_path


def read_source():
    """Read signal_analyser.py source."""
    with open(SIGNAL_ANALYSER_PATH, 'r') as f:
        return f.read()


def write_source(content):
    """Write signal_analyser.py source."""
    with open(SIGNAL_ANALYSER_PATH, 'w') as f:
        f.write(content)
    log(f"Wrote patched signal_analyser.py")


def find_score_supply_chain_func(source):
    """Locate score_supply_chain function by name pattern."""
    pattern = r'def score_supply_chain\s*\('
    match = re.search(pattern, source)
    if match:
        return match.start()
    return -1


def extract_function_body(source, start_pos):
    """Extract function body until next def/class at same indent or EOF."""
    lines = source.split('\n')
    func_line_idx = -1
    for i, line in enumerate(lines):
        if line.strip().startswith('def score_supply_chain'):
            func_line_idx = i
            break
    
    if func_line_idx == -1:
        return None, -1
    
    func_indent = len(lines[func_line_idx]) - len(lines[func_line_idx].lstrip())
    body_start = func_line_idx + 1
    body_end = body_start
    
    for i in range(body_start, len(lines)):
        line = lines[i]
        if line.strip() == '':
            body_end = i + 1
            continue
        if line.strip().startswith('#') and not line.strip().startswith('# _'):
            body_end = i + 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= func_indent and line.strip():
            break
        body_end = i + 1
    
    body_lines = lines[body_start:body_end]
    return '\n'.join(body_lines), body_start


def build_threat_intel_query(server_id):
    """Build threat intel query SQL."""
    sources_list = ','.join(f"'{s}'" for s in THREAT_SOURCES)
    sql = f"""
    SELECT severity, source, threat_type, evidence, reported_at
    FROM mcp_threat_associations
    WHERE server_id = '{server_id}' AND source IN ({sources_list})
    ORDER BY reported_at DESC LIMIT 10
    """
    return sql


def generate_new_score_supply_chain(original_body):
    """Generate new score_supply_chain with threat intel integration."""
    new_body = f"""    # {MARKER}
    def score_supply_chain(server_id, name):
        # Step 1: Query threat intel feeds via mcp_threat_associations
        sources_list = ','.join(f"'{s}'" for s in {THREAT_SOURCES})
        threat_sql = f\"\"\"
        SELECT severity, source, threat_type, evidence, reported_at
        FROM mcp_threat_associations
        WHERE server_id = '{{server_id}}' AND source IN ({{sources_list}})
        ORDER BY reported_at DESC LIMIT 10
        \"\"\"
        
        import requests
        try:
            threat_resp = requests.post('{QUERY_URL}', json={{'sql': threat_sql}}, timeout=15)
            if threat_resp.status_code == 200:
                result = threat_resp.json()
                threat_rows = result.get('rows', []) if isinstance(result, dict) else result
                if threat_rows:
                    # Pick worst severity
                    severity_scores = {{'CRITICAL': 0.0, 'HIGH': 15.0, 'MEDIUM': 40.0, 'LOW': 60.0}}
                    worst_score = 50.0
                    evidence_parts = []
                    for row in threat_rows:
                        sev = row.get('severity', '').strip().upper()
                        score = severity_scores.get(sev, 50.0)
                        if score < worst_score:
                            worst_score = score
                        src = row.get('source', 'unknown')
                        ttype = row.get('threat_type', 'unknown')
                        ev_text = row.get('evidence', '') or ''
                        if len(ev_text) > 80:
                            ev_text = ev_text[:80]
                        evidence_parts.append(f'{{src}}: {{ttype}} — {{ev_text}}')
                    evidence = ' | '.join(evidence_parts[:3])
                    return (worst_score, evidence)
        except Exception as e:
            pass  # Fall through to regex check
        
        # Step 2: No threat intel found — use existing regex with prefix
        import re
        from known_threats import check_package, check_domain, HIGH_RISK_PATTERNS
        
        # Check known-threat list
        cp = check_package(name)
        if cp:
            return (0.0, f"package_blocked={cp}")
        cd = check_domain(name)
        if cd:
            return (0.0, f"domain_blocked={cd}")
        
        # Check suspicious patterns
        patterns = [
            (r'auth[_-]?token[_-]?harvest', 15.0, "auth_token_harvest"),
            (r'credential[_-]?steal', 15.0, "credential_stealer"),
            (r'keylog', 15.0, "keylogger"),
            (r'steal|exfil|data[_-]?loot', 20.0, "data_exfil"),
            (r'backdoor|trojan|rAT', 10.0, "backdoor"),
            (r'shell[_-]?exec|reverse[_-]?shell', 10.0, "shell_execution"),
            (r'sudo|root[_-]?escal|privilege[_-]?esc', 30.0, "priv_esc"),
            (r'cryptominer|crypto[_-]?miner', 15.0, "cryptominer"),
            (r'ransomware|ransom', 5.0, "ransomware"),
            (r'botnet|bot[_-]?net', 10.0, "botnet"),
        ]
        
        name_lower = name.lower()
        for pattern, score, label in patterns:
            if re.search(pattern, name_lower):
                return (score, f"no_threat_intel_match — pattern={label}")
        
        return (50.0, "no_threat_intel_match — benign_check passed")

"""
    return new_body


def apply_patch():
    """Apply the patch to signal_analyser.py."""
    log("Starting patch application...")
    
    source = read_source()
    
    # Check idempotence
    if MARKER in source:
        log(f"Already patched (marker found). Exiting 0.")
        return 0
    
    # Backup
    backup_file(SIGNAL_ANALYSER_PATH)
    
    # Find function
    start_pos = find_score_supply_chain_func(source)
    if start_pos == -1:
        log("ERROR: score_supply_chain not found in signal_analyser.py")
        return 1
    
    # Extract function
    body, body_start = extract_function_body(source, start_pos)
    if body is None:
        log("ERROR: Could not extract function body")
        return 1
    
    log(f"Found score_supply_chain at line {body_start + 1}")
    
    # Build new version
    new_body = generate_new_score_supply_chain(body)
    
    # Replace in source
    lines = source.split('\n')
    new_lines = lines[:body_start] + new_body.split('\n') + lines[body_start + len(body.split('\n')):]
    new_source = '\n'.join(new_lines)
    
    if DRY_RUN:
        log("DRY-RUN: would patch signal_analyser.py")
        log(f"New score_supply_chain function length: {len(new_body)} chars")
        return 3
    
    write_source(new_source)
    log("Patch applied successfully")
    return 0


def smoke_test():
    """Run smoke tests to verify patch."""
    log("Running smoke tests...")
    
    import requests
    
    # Ensure table exists
    ensure_threat_associations_table()
    
    test_server_id = "SMOKE_TEST_OTX"
    normal_server_id = "NO_INTEL_HASH_SMOKE"
    
    try:
        # Test 1: Insert synthetic threat intel
        ws_write('mcp_threat_associations', {
            'server_id': test_server_id,
            'source': 'alienvault_otx',
            'threat_type': 'c2_indicator',
            'severity': 'HIGH',
            'evidence': 'synthetic test indicator'
        })
        log("Inserted synthetic threat_associations row")
        
        # Wait for write to complete
        time.sleep(1)
        
        # Reload signal_analyser module
        import importlib
        import signal_analyser
        importlib.reload(signal_analyser)
        
        # Test 2: Call with intel server
        score, evidence = signal_analyser.score_supply_chain(test_server_id, 'test')
        log(f"score_supply_chain('{test_server_id}', 'test') = ({score}, '{evidence}')")
        
        if score != 15.0:
            log(f"FAIL: Expected score 15.0, got {score}")
            return False
        if 'alienvault_otx' not in evidence:
            log(f"FAIL: Expected 'alienvault_otx' in evidence, got '{evidence}'")
            return False
        
        # Test 3: Call with no-intel server
        score2, evidence2 = signal_analyser.score_supply_chain(normal_server_id, 'normal-package')
        log(f"score_supply_chain('{normal_server_id}', 'normal-package') = ({score2}, '{evidence2}')")
        
        if not evidence2.startswith('no_threat_intel_match'):
            log(f"FAIL: Expected evidence to start with 'no_threat_intel_match', got '{evidence2}'")
            return False
        
        # Test 4: Cleanup
        ws_execute(f"DELETE FROM mcp_threat_associations WHERE server_id = '{test_server_id}'")
        ws_execute(f"DELETE FROM mcp_threat_associations WHERE server_id = '{normal_server_id}'")
        log("Cleaned up synthetic rows")
        
        log("All smoke tests PASSED")
        return True
        
    except Exception as e:
        log(f"Smoke test exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def restore_backup():
    """Restore from backup."""
    backup_path = SIGNAL_ANALYSER_PATH + BACKUP_SUFFIX
    if os.path.exists(backup_path):
        shutil.copy2(backup_path, SIGNAL_ANALYSER_PATH)
        log("Restored from backup")
        return True
    log("WARNING: No backup found to restore")
    return False


def main():
    log("Starting r2_score_supply_chain_threat_assoc_patcher...")
    
    # Apply patch
    patch_result = apply_patch()
    if patch_result != 0:
        sys.exit(patch_result)
    
    if DRY_RUN:
        log("Dry-run complete. Exit 3 (changes needed).")
        sys.exit(3)
    
    # Run smoke tests
    smoke_ok = smoke_test()
    
    if not smoke_ok:
        log("SMOKE TEST FAILED — restoring backup and exiting 2")
        restore_backup()
        sys.exit(2)
    
    log("SUCCESS — patch applied and smoke tests passed. Exit 0.")
    sys.exit(0)


if __name__ == '__main__':
    main()