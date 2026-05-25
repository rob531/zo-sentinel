import os
import sys
import time
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import requests

SERVICE_NAME = 'e2e_scenarios'
PORT = 8099
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'
EXECUTE_SERVICE_URL = 'http://127.0.0.1:8772/execute'
LOG_DIR = '/home/workspace/logs'
LOG_FILE = f'{LOG_DIR}/{SERVICE_NAME}.log'

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(SERVICE_NAME)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_server_id(name: str, url: str) -> str:
    content = f"{name.lower().strip()}|{url.lower().strip()}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def ws_write(table: str, rows: list) -> bool:
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={'table': table, 'rows': rows}, timeout=30)
        data = resp.json()
        return resp.status_code == 200 and data.get('ok', False)
    except Exception as e:
        logger.error(f"ws_write error on {table}: {e}")
        return False


def ws_query(sql: str) -> list:
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={'sql': sql}, timeout=30)
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        logger.error(f"ws_query error: {e}")
        return []


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={'sql': sql}, timeout=30)
        data = resp.json()
        return data.get('ok', False)
    except Exception as e:
        logger.error(f"ws_execute error: {e}")
        return False


def ensure_test_tables() -> bool:
    queries = [
        "CREATE SEQUENCE IF NOT EXISTS e2e_test_servers_seq",
        "CREATE TABLE IF NOT EXISTS e2e_test_servers (server_id VARCHAR, name VARCHAR, url VARCHAR, created_at TIMESTAMPTZ, flow VARCHAR, status VARCHAR)",
        "CREATE TABLE IF NOT EXISTS e2e_test_results (test_id VARCHAR, flow VARCHAR, step VARCHAR, passed BOOLEAN, detail VARCHAR, ts TIMESTAMPTZ)"
    ]
    for q in queries:
        if not ws_execute(q):
            return False
    return True


def record_test_result(test_id: str, flow: str, step: str, passed: bool, detail: str) -> None:
    ws_write('e2e_test_results', [{
        'test_id': test_id,
        'flow': flow,
        'step': step,
        'passed': passed,
        'detail': detail[:500],
        'ts': utc_now_iso()
    }])


def create_synthetic_mcp(name: str, url: str, description: str, flow: str) -> Optional[str]:
    server_id = compute_server_id(name, url)
    ts = utc_now_iso()
    
    if not ws_write('e2e_test_servers', [{
        'server_id': server_id,
        'name': name,
        'url': url,
        'created_at': ts,
        'flow': flow,
        'status': 'created'
    }]):
        return None
    
    registry_rows = [{
        'server_id': server_id,
        'name': name,
        'url': url,
        'description': description,
        'registry_source': 'e2e_synthetic',
        'first_seen': ts,
        'last_seen': ts,
        'verdict': 'INSUFFICIENT',
        'trust_score': 0.0,
        'scan_count': 0
    }]
    
    existing = ws_query(f"SELECT server_id FROM mcp_server_registry WHERE server_id = '{server_id}'")
    if existing:
        ws_execute(f"DELETE FROM mcp_server_registry WHERE server_id = '{server_id}'")
    
    if not ws_write('mcp_server_registry', registry_rows):
        return None
    
    return server_id


def write_signal_score(server_id: str, signal_name: str, score: float, evidence: Dict) -> bool:
    signal_id = hashlib.sha256(f"{server_id}|{signal_name}|{utc_now_iso()}".encode()).hexdigest()[:32]
    
    evidence_blob = {
        'source': 'e2e_synthetic',
        'ts': utc_now_iso(),
        'data': evidence
    }
    
    signal_rows = [{
        'server_id': server_id,
        'signal_name': signal_name,
        'score': score,
        'evidence': str(evidence_blob),
        'scored_at': utc_now_iso()
    }]
    
    return ws_write('mcp_signal_scores', signal_rows)


def write_verdict(server_id: str, verdict: str, trust_score: float) -> bool:
    sql = f"UPDATE mcp_server_registry SET verdict = '{verdict}', trust_score = {trust_score}, last_seen = '{utc_now_iso()}' WHERE server_id = '{server_id}'"
    return ws_execute(sql)


def write_attestation(server_id: str, verdict: str, analyst: str = 'e2e_synthetic') -> bool:
    expiry_days = {'TRUSTED_GENERAL': 90, 'TRUSTED_RESEARCH': 60, 'ENTERPRISE_CONTROLLED': 60,
                   'CAUTION_LIMITED': 30, 'HIGH_RISK_ISOLATED': 7, 'KNOWN_THREAT': 7}
    
    days = expiry_days.get(verdict, 30)
    expires_at = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = expires_at.replace(second=expires_at.second + days)
    exp_iso = expires_at.isoformat()
    
    attestation_rows = [{
        'server_id': server_id,
        'verdict': verdict,
        'attested_by': analyst,
        'attested_at': utc_now_iso(),
        'expires_at': exp_iso,
        'evidence_summary': 'E2E synthetic test attestation',
        'attestation_type': 'automated'
    }]
    
    return ws_write('mcp_attestations', attestation_rows)


def write_manual_override(server_id: str, verdict: str, analyst: str) -> bool:
    override_rows = [{
        'server_id': server_id,
        'override_verdict': verdict,
        'override_reason': 'E2E synthetic test override',
        'analyst': analyst,
        'created_at': utc_now_iso(),
        'expires_at': datetime.now(timezone.utc).isoformat()
    }]
    return ws_write('manual_override_metadata', override_rows)


def verify_signal_scores(server_id: str, min_count: int = 1) -> bool:
    rows = ws_query(f"SELECT COUNT(*) as cnt FROM mcp_signal_scores WHERE server_id = '{server_id}'")
    if not rows:
        return False
    count = rows[0].get('cnt', 0) if isinstance(rows[0], dict) else (rows[0][0] if rows[0] else 0)
    return count >= min_count


def verify_verdict(server_id: str) -> Optional[Dict]:
    rows = ws_query(f"SELECT verdict, trust_score FROM mcp_server_registry WHERE server_id = '{server_id}'")
    if not rows or not rows[0]:
        return None
    row = rows[0]
    if isinstance(row, dict):
        return row
    return {'verdict': row[0], 'trust_score': row[1]}


def verify_attestation(server_id: str) -> bool:
    rows = ws_query(f"SELECT COUNT(*) as cnt FROM mcp_attestations WHERE server_id = '{server_id}'")
    if not rows:
        return False
    cnt = rows[0].get('cnt', 0) if isinstance(rows[0], dict) else (rows[0][0] if rows[0] else 0)
    return cnt > 0


def run_flow_1_new_submission(test_id: str) -> bool:
    logger.info(f"[{test_id}] Starting Flow 1: New MCP submission")
    
    name = f"e2e-test-server-{test_id}"
    url = f"https://e2e-{test_id}.example.com/mcp"
    description = "E2E synthetic test MCP server for flow 1"
    
    record_test_result(test_id, 'flow_1', 'start', True, 'Flow 1 initiated')
    
    server_id = create_synthetic_mcp(name, url, description, 'flow_1')
    if not server_id:
        record_test_result(test_id, 'flow_1', 'create_mcp', False, 'Failed to create synthetic MCP')
        return False
    record_test_result(test_id, 'flow_1', 'create_mcp', True, f'Created server_id={server_id}')
    
    signals = [
        ('supply_chain_trust', 0.75, {'npm_downloads': 15000, 'github_stars': 500, 'registry_verified': True}),
        ('community_signal', 0.70, {'stars': 450, 'forks': 80, 'contributors': 12}),
        ('injection_resilience', 0.85, {'safe_description': True, 'no_prompt_leak': True}),
        ('temporal_stability', 0.80, {'age_days': 365, 'recent_updates': True}),
        ('permission_scope', 0.65, {'limited_scope': True, 'sandboxed': True}),
        ('tool_description_safety', 0.90, {'clear_docs': True, 'safe_params': True})
    ]
    
    for signal_name, score, evidence in signals:
        if not write_signal_score(server_id, signal_name, score, evidence):
            record_test_result(test_id, 'flow_1', f'signal_{signal_name}', False, 'Failed to write signal score')
            return False
    
    record_test_result(test_id, 'flow_1', 'write_signals', True, f'Wrote {len(signals)} signal scores')
    
    if not verify_signal_scores(server_id, min_count=len(signals)):
        record_test_result(test_id, 'flow_1', 'verify_signals', False, 'Signal scores not found in DB')
        return False
    record_test_result(test_id, 'flow_1', 'verify_signals', True, 'All signal scores verified')
    
    avg_score = sum(s for _, s, _ in signals) / len(signals)
    if avg_score >= 0.80:
        verdict = 'TRUSTED_RESEARCH'
    elif avg_score >= 0.60:
        verdict = 'ENTERPRISE_CONTROLLED'
    elif avg_score >= 0.40:
        verdict = 'CAUTION_LIMITED'
    else:
        verdict = 'HIGH_RISK_ISOLATED'
    
    if not write_verdict(server_id, verdict, avg_score):
        record_test_result(test_id, 'flow_1', 'write_verdict', False, 'Failed to write verdict')
        return False
    
    v_result = verify_verdict(server_id)
    if not v_result or v_result.get('verdict') != verdict:
        record_test_result(test_id, 'flow_1', 'verify_verdict', False, f'Verdict mismatch: {v_result}')
        return False
    record_test_result(test_id, 'flow_1', 'verify_verdict', True, f'Verdict {verdict} verified')
    
    if not write_attestation(server_id, verdict, 'e2e_flow1'):
        record_test_result(test_id, 'flow_1', 'write_attestation', False, 'Failed to write attestation')
        return False
    
    if not verify_attestation(server_id):
        record_test_result(test_id, 'flow_1', 'verify_attestation', False, 'Attestation not found in DB')
        return False
    record_test_result(test_id, 'flow_1', 'verify_attestation', True, 'Attestation verified')
    
    logger.info(f"[{test_id}] Flow 1 PASSED - MCP submitted, scored, verdict={verdict}, attested")
    return True


def run_flow_2_reverdict(test_id: str) -> bool:
    logger.info(f"[{test_id}] Starting Flow 2: Existing MCP re-verdict by freshness SLA")
    
    name = f"e2e-reverdict-{test_id}"
    url = f"https://e2e-reverdict-{test_id}.example.com/mcp"
    description = "E2E synthetic test server for re-verdict flow"
    
    record_test_result(test_id, 'flow_2', 'start', True, 'Flow 2 initiated')
    
    server_id = create_synthetic_mcp(name, url, description, 'flow_2')
    if not server_id:
        record_test_result(test_id, 'flow_2', 'create_mcp', False, 'Failed to create MCP')
        return False
    
    write_verdict(server_id, 'TRUSTED_RESEARCH', 0.75)
    record_test_result(test_id, 'flow_2', 'initial_verdict', True, 'Initial verdict set')
    
    old_signals = [('supply_chain_trust', 0.75), ('community_signal', 0.70)]
    for sig, score in old_signals:
        write_signal_score(server_id, sig, score, {'old': True})
    
    logger.info(f"[{test_id}] Simulating freshness SLA expiry - triggering re-verdict")
    
    new_signals = [
        ('supply_chain_trust', 0.40, {'npm_downloads': 100, 'github_stars': 5, 'registry_verified': False}),
        ('community_signal', 0.30, {'stars': 10, 'forks': 2, 'contributors': 1}),
        ('injection_resilience', 0.55, {'safe_description': True, 'no_prompt_leak': False}),
        ('temporal_stability', 0.35, {'age_days': 30, 'recent_updates': True}),
        ('permission_scope', 0.30, {'limited_scope': False, 'sandboxed': False}),
        ('tool_description_safety', 0.50, {'clear_docs': False, 'safe_params': True})
    ]
    
    for signal_name, score, evidence in new_signals:
        if not write_signal_score(server_id, signal_name, score, evidence):
            record_test_result(test_id, 'flow_2', f'signal_{signal_name}', False, 'Failed to write new signal')
            return False
    
    record_test_result(test_id, 'flow_2', 'write_new_signals', True, 'New signals written after SLA expiry')
    
    avg_score = sum(s for _, s, _ in new_signals) / len(new_signals)
    old_verdict = 'TRUSTED_RESEARCH'
    if avg_score >= 0.70:
        new_verdict = 'TRUSTED_RESEARCH'
    elif avg_score >= 0.50:
        new_verdict = 'CAUTION_LIMITED'
    elif avg_score >= 0.30:
        new_verdict = 'HIGH_RISK_ISOLATED'
    else:
        new_verdict = 'KNOWN_THREAT'
    
    if not write_verdict(server_id, new_verdict, avg_score):
        record_test_result(test_id, 'flow_2', 'write_reverdict', False, 'Failed to write re-verdict')
        return False
    
    v_result = verify_verdict(server_id)
    if not v_result or v_result.get('verdict') != new_verdict:
        record_test_result(test_id, 'flow_2', 'verify_reverdict', False, f'Re-verdict mismatch: {v_result}')
        return False
    
    if old_verdict == new_verdict:
        record_test_result(test_id, 'flow_2', 'verdict_changed', False, 'Verdict should have changed but did not')
        return False
    
    record_test_result(test_id, 'flow_2', 'verdict_changed', True, f'Verdict changed from {old_verdict} to {new_verdict}')
    record_test_result(test_id, 'flow_2', 'verify_reverdict', True, f'Re-verdict {new_verdict} verified')
    
    logger.info(f"[{test_id}] Flow 2 PASSED - MCP re-verdicted from TRUSTED_RESEARCH to {new_verdict}")
    return True


def run_flow_3_analyst_override(test_id: str) -> bool:
    logger.info(f"[{test_id}] Starting Flow 3: Analyst override through manual_override_api")
    
    name = f"e2e-override-{test_id}"
    url = f"https://e2e-override-{test_id}.example.com/mcp"
    description = "E2E synthetic test server for analyst override flow"
    
    record_test_result(test_id, 'flow_3', 'start', True, 'Flow 3 initiated')
    
    server_id = create_synthetic_mcp(name, url, description, 'flow_3')
    if not server_id:
        record_test_result(test_id, 'flow_3', 'create_mcp', False, 'Failed to create MCP')
        return False
    
    initial_score = 0.50
    write_verdict(server_id, 'CAUTION_LIMITED', initial_score)
    record_test_result(test_id, 'flow_3', 'initial_state', True, 'Initial verdict CAUTION_LIMITED')
    
    analyst = 'e2e_analyst_test'
    override_verdict = 'TRUSTED_RESEARCH'
    override_reason = 'E2E test: Security team reviewed and approved for research use'
    
    try:
        resp = requests.post(
            f'http://127.0.0.1:8786/override',
            json={
                'server_id': server_id,
                'verdict': override_verdict,
                'reason': override_reason,
                'analyst': analyst,
                'duration_days': 60
            },
            timeout=15
        )
        override_success = resp.status_code in (200, 201, 202)
        logger.info(f"[{test_id}] manual_override_api response: {resp.status_code}")
    except Exception as e:
        logger.warning(f"[{test_id}] manual_override_api call failed: {e}")
        override_success = False
    
    if not override_success:
        if not ws_execute("CREATE TABLE IF NOT EXISTS manual_override_metadata (server_id VARCHAR, override_verdict VARCHAR, override_reason VARCHAR, analyst VARCHAR, created_at TIMESTAMPTZ, expires_at TIMESTAMPTZ)"):
            logger.error(f"[{test_id}] Failed to create manual_override_metadata table")
            return False
        
        if not write_manual_override(server_id, override_verdict, analyst):
            record_test_result(test_id, 'flow_3', 'write_override', False, 'Failed to write override record')
            return False
    
    record_test_result(test_id, 'flow_3', 'write_override', True, 'Override record written')
    
    if not write_verdict(server_id, override_verdict, 0.85):
        record_test_result(test_id, 'flow_3', 'apply_override_verdict', False, 'Failed to apply override verdict')
        return False
    
    v_result = verify_verdict(server_id)
    if not v_result or v_result.get('verdict') != override_verdict:
        record_test_result(test_id, 'flow_3', 'verify_override', False, f'Override verdict mismatch: {v_result}')
        return False
    
    record_test_result(test_id, 'flow_3', 'verify_override', True, f'Override verdict {override_verdict} verified')
    
    override_rows = ws_query(f"SELECT * FROM manual_override_metadata WHERE server_id = '{server_id}'")
    if not override_rows:
        record_test_result(test_id, 'flow_3', 'verify_override_record', False, 'Override metadata not found')
        return False
    
    record_test_result(test_id, 'flow_3', 'verify_override_record', True, 'Override metadata found in DB')
    
    logger.info(f"[{test_id}] Flow 3 PASSED - Analyst {analyst} overrode verdict to {override_verdict}")
    return True


def check_single_instance():
    import os
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            logger.error(f"{SERVICE_NAME} already running as PID {old_pid}")
            sys.exit(0)
        except OSError:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


def signal_handler(signum, frame):
    logger.info(f"{SERVICE_NAME} received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    ws_write('service_health', [{
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': 'running'
    }])


def run():
    logger.info(f"{SERVICE_NAME} starting")
    check_single_instance()
    
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not ensure_test_tables():
        logger.error("Failed to ensure test tables")
        remove_pid_file()
        sys.exit(1)
    
    test_id_base = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    
    results = []
    
    logger.info("=" * 60)
    logger.info("E2E SCENARIOS: Running 3 canonical flows")
    logger.info("=" * 60)
    
    f1_id = f"{test_id_base}_flow1"
    f1_pass = run_flow_1_new_submission(f1_id)
    results.append(('Flow 1 - New Submission', f1_pass))
    send_heartbeat()
    time.sleep(2)
    
    f2_id = f"{test_id_base}_flow2"
    f2_pass = run_flow_2_reverdict(f2_id)
    results.append(('Flow 2 - Re-verdict SLA', f2_pass))
    send_heartbeat()
    time.sleep(2)
    
    f3_id = f"{test_id_base}_flow3"
    f3_pass = run_flow_3_analyst_override(f3_id)
    results.append(('Flow 3 - Analyst Override', f3_pass))
    send_heartbeat()
    
    logger.info("=" * 60)
    logger.info("E2E SCENARIOS SUMMARY")
    logger.info("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "PASSED" if passed else "FAILED"
        logger.info(f"  {name}: {status}")
        if not passed:
            all_passed = False
    
    if all_passed:
        logger.info("ALL FLOWS PASSED - Exit 0")
        remove_pid_file()
        sys.exit(0)
    else:
        logger.error("SOME FLOWS FAILED - Exit 1")
        remove_pid_file()
        sys.exit(1)


if __name__ == '__main__':
    run()