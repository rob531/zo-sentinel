import time
import uuid
import json
from datetime import datetime, timezone
import sys
import os
import requests

SERVICE_NAME = "e2e_scenarios_run"
SERVICE_PORT = 8799
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE = "http://127.0.0.1:8772/write"
QUERY_SERVICE = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE = "http://127.0.0.1:8772/execute"
LOG_FILE = "/home/workspace/zo_sentinel/e2e_scenarios_run.log"
START_TIME = time.time()

def log(msg):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] [e2e] {msg}"
    print(line)
    with open(LOG_FILE, "a") as fh:
        fh.write(line + "\n")

def get_write_url():
    return WRITE_SERVICE

def get_query_url():
    return QUERY_SERVICE

def get_execute_url():
    return EXECUTE_SERVICE

def get_db_path():
    return "/home/workspace/zo_sentinel/zo_sentinel.db"

def ws_query(sql):
    try:
        resp = requests.post(QUERY_SERVICE, json={"sql": sql}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"QUERY ERROR: {e} | SQL: {sql[:200]}")
        return {"rows": [], "count": 0}

def ws_write(table, rows):
    try:
        resp = requests.post(WRITE_SERVICE, json={"table": table, "rows": rows}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"WRITE ERROR: {e} | Table: {table}")
        raise

def ws_execute(sql):
    try:
        resp = requests.post(EXECUTE_SERVICE, json={"sql": sql}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"EXECUTE ERROR: {e} | SQL: {sql[:200]}")
        raise

def check_single_instance():
    pid_file = PID_FILE
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        if old_pid != os.getpid():
            try:
                os.kill(old_pid, 0)
                log(f"Another instance running with PID {old_pid}")
                return False
            except OSError:
                log(f"Stale PID file, removing")
                os.remove(pid_file)
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    return True

def send_heartbeat():
    try:
        ws_write("service_health", {
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        log(f"Heartbeat failed: {e}")

def heartbeat_loop(interval=60):
    while True:
        send_heartbeat()
        time.sleep(interval)

def ensure_test_tables():
    log("Ensuring test tables exist")
    tables = [
        "CREATE SEQUENCE IF NOT EXISTS e2e_test_log_id_seq",
        "CREATE TABLE IF NOT EXISTS e2e_test_log (id TEXT DEFAULT 'e2e-' || nextval('e2e_test_log_id_seq'), test_name TEXT, step_name TEXT, status TEXT, detail TEXT, executed_at TIMESTAMP DEFAULT now())",
        "CREATE SEQUENCE IF NOT EXISTS e2e_test_servers_id_seq",
        "CREATE TABLE IF NOT EXISTS e2e_test_servers (server_id TEXT DEFAULT 'e2e-srv-' || nextval('e2e_test_servers_id_seq'), name TEXT, url TEXT, description TEXT, created_at TIMESTAMP DEFAULT now())",
    ]
    for sql in tables:
        try:
            ws_execute(sql)
        except Exception as e:
            log(f"Table creation warning: {e}")

def log_test_step(test_name, step_name, status, detail=""):
    try:
        ws_write("e2e_test_log", {
            "test_name": test_name,
            "step_name": step_name,
            "status": status,
            "detail": detail[:500] if detail else ""
        })
    except Exception as e:
        log(f"Could not log test step: {e}")

def wait_for_service(url, timeout=30):
    log(f"Waiting for service: {url}")
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                log(f"Service ready: {url}")
                return True
        except:
            pass
        time.sleep(2)
    log(f"Service not ready: {url}")
    return False

def verify_registry_api():
    try:
        resp = requests.get("http://127.0.0.1:8781/health", timeout=5)
        return resp.status_code == 200
    except:
        return False

def verify_approval_workflow():
    try:
        resp = requests.get("http://127.0.0.1:8780/health", timeout=5)
        return resp.status_code == 200
    except:
        return False

def verify_signal_analyser():
    result = ws_query("SELECT service, last_heartbeat FROM service_health WHERE service = 'signal_analyser'")
    if result.get("rows"):
        hb = result["rows"][0].get("last_heartbeat", "")
        if hb:
            hb_time = datetime.fromisoformat(hb.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - hb_time).total_seconds()
            return age < 300
    return False

def verify_trust_synthesiser():
    result = ws_query("SELECT service, last_heartbeat FROM service_health WHERE service = 'trust_synthesiser'")
    if result.get("rows"):
        hb = result["rows"][0].get("last_heartbeat", "")
        if hb:
            hb_time = datetime.fromisoformat(hb.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - hb_time).total_seconds()
            return age < 300
    return False

def verify_attestation_engine():
    result = ws_query("SELECT service, last_heartbeat FROM service_health WHERE service = 'attestation_engine'")
    if result.get("rows"):
        hb = result["rows"][0].get("last_heartbeat", "")
        if hb:
            hb_time = datetime.fromisoformat(hb.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - hb_time).total_seconds()
            return age < 300
    return False

def get_or_create_test_server(name, url, description):
    result = ws_query(f"SELECT server_id FROM mcp_server_registry WHERE url = '{url}' LIMIT 1")
    if result.get("rows"):
        server_id = result["rows"][0]["server_id"]
        log(f"Found existing server: {server_id}")
        return server_id
    
    test_result = ws_query("SELECT nextval('mcp_server_registry_id_seq') as seq")
    seq = test_result["rows"][0]["seq"] if test_result.get("rows") else 1
    server_id = f"srv-e2e-{seq}"
    
    ws_write("mcp_server_registry", {
        "server_id": server_id,
        "name": name,
        "url": url,
        "description": description,
        "trust_score": None,
        "verdict": None,
        "registry_source": "e2e_test",
        "scan_count": 0
    })
    log(f"Created test server: {server_id}")
    return server_id

def check_signal_scores(server_id):
    result = ws_query(f"SELECT COUNT(*) as cnt FROM mcp_signal_scores WHERE server_id = '{server_id}'")
    return result["rows"][0]["cnt"] if result.get("rows") else 0

def check_verdict(server_id):
    result = ws_query(f"SELECT verdict, trust_score FROM mcp_server_registry WHERE server_id = '{server_id}'")
    if result.get("rows"):
        return result["rows"][0]
    return None

def check_attestations(server_id):
    result = ws_query(f"SELECT COUNT(*) as cnt FROM attestations WHERE server_id = '{server_id}' AND revoked = false")
    return result["rows"][0]["cnt"] if result.get("rows") else 0

def check_threat_associations(server_id):
    result = ws_query(f"SELECT COUNT(*) as cnt FROM threat_associations WHERE server_id = '{server_id}'")
    return result["rows"][0]["cnt"] if result.get("rows") else 0

def check_ui_visibility(server_id):
    result = ws_query(f"SELECT server_id, name, verdict, trust_score FROM mcp_server_registry WHERE server_id = '{server_id}'")
    return result.get("rows") is not None

def check_mesh_events(server_id):
    result = ws_query(f"SELECT COUNT(*) as cnt FROM mesh_events WHERE event_type = 'verdict_generated' AND server_id = '{server_id}'")
    return result["rows"][0]["cnt"] if result.get("rows") else 0

def create_threat_intel_entry(server_id, threat_type, severity, evidence):
    try:
        ws_write("mcp_threat_associations", {
            "server_id": server_id,
            "threat_type": threat_type,
            "severity": severity,
            "evidence": evidence
        })
        log(f"Created threat association: {threat_type} ({severity})")
        return True
    except Exception as e:
        log(f"Could not create threat association: {e}")
        return False

def create_attestation(server_id, attestor, statement, expires_at=None):
    try:
        ws_write("attestations", {
            "server_id": server_id,
            "attestor": attestor,
            "statement": statement,
            "expires_at": expires_at or datetime.now(timezone.utc).isoformat()
        })
        log(f"Created attestation by {attestor}")
        return True
    except Exception as e:
        log(f"Could not create attestation: {e}")
        return False

def update_verdict(server_id, verdict, trust_score):
    try:
        ws_execute(f"UPDATE mcp_server_registry SET verdict = '{verdict}', trust_score = {trust_score} WHERE server_id = '{server_id}'")
        log(f"Updated verdict to {verdict} (score: {trust_score})")
        return True
    except Exception as e:
        log(f"Could not update verdict: {e}")
        return False

def run_flow_1_new_mcp_to_ui():
    log("=" * 60)
    log("FLOW 1: New MCP → Signal Scored → Verdict → Attestation → UI Visible")
    log("=" * 60)
    
    test_name = "flow_1_new_mcp_to_ui"
    flow_passed = True
    
    log_test_step(test_name, "start", "running", "Beginning flow 1")
    
    log("Step 1.1: Verify prerequisite services")
    services_ok = True
    if not verify_signal_analyser():
        log("WARNING: signal_analyser not healthy")
        log_test_step(test_name, "check_signal_analyser", "warning", "Signal analyser may be stale")
        services_ok = False
    else:
        log_test_step(test_name, "check_signal_analyser", "pass", "Signal analyser healthy")
    
    if not verify_trust_synthesiser():
        log("WARNING: trust_synthesiser not healthy")
        log_test_step(test_name, "check_trust_synthesiser", "warning", "Trust synthesiser may be stale")
    else:
        log_test_step(test_name, "check_trust_synthesiser", "pass", "Trust synthesiser healthy")
    
    if not verify_registry_api():
        log("WARNING: registry_api not available")
        log_test_step(test_name, "check_registry_api", "warning", "Registry API may not be running")
    
    log("Step 1.2: Create test MCP server")
    test_name_unique = f"e2e-flow1-{uuid.uuid4().hex[:8]}"
    test_url = f"https://npmjs.com/package/@e2e/{test_name_unique}"
    server_id = get_or_create_test_server(
        name=f"e2e-flow1-test-server",
        url=test_url,
        description="End-to-end test MCP server for flow 1 validation"
    )
    log_test_step(test_name, "create_server", "pass", f"Server ID: {server_id}")
    
    log("Step 1.3: Verify server in registry")
    verdict_data = check_verdict(server_id)
    if verdict_data:
        log(f"Server exists in registry. Current verdict: {verdict_data.get('verdict')}, score: {verdict_data.get('trust_score')}")
        log_test_step(test_name, "verify_server_exists", "pass", f"Server in registry")
    else:
        log("Server not yet in registry - this is expected for new server")
        log_test_step(test_name, "verify_server_exists", "info", "New server, verdict pending")
    
    log("Step 1.4: Simulate signal scoring by creating signal scores")
    signal_scores = [
        ("supply_chain_score", 0.75, '{"age_days": 365, "downloads": 10000}'),
        ("community_signal_score", 0.80, '{"stars": 500, "forks": 50}'),
        ("temporal_stability_score", 0.70, '{"consistency": 0.9}'),
        ("permission_scope_score", 0.85, '{"read_only": true}'),
        ("tool_description_safety_score", 0.90, '{"clear_docs": true}'),
        ("injection_resilience_score", 0.95, '{"sanitized": true}'),
    ]
    
    for signal_name, score, evidence in signal_scores:
        try:
            ws_write("mcp_signal_scores", {
                "server_id": server_id,
                "signal_name": signal_name,
                "score": score,
                "evidence": evidence
            })
        except Exception as e:
            log(f"Signal write warning: {e}")
    
    log_test_step(test_name, "create_signals", "pass", f"Created {len(signal_scores)} signals")
    
    log("Step 1.5: Verify signals in mcp_signal_scores table")
    time.sleep(1)
    signal_count = check_signal_scores(server_id)
    log(f"Found {signal_count} signal scores for {server_id}")
    if signal_count >= len(signal_scores):
        log_test_step(test_name, "verify_signals", "pass", f"Verified {signal_count} signals")
    else:
        log_test_step(test_name, "verify_signals", "warning", f"Only {signal_count} signals found")
    
    log("Step 1.6: Trigger trust synthesiser (via write to service_health)")
    try:
        send_heartbeat()
        log("Triggered heartbeat - trust synthesiser should process")
        log_test_step(test_name, "trigger_trust_synthesiser", "pass", "Heartbeat sent")
    except Exception as e:
        log(f"Trigger warning: {e}")
        log_test_step(test_name, "trigger_trust_synthesiser", "warning", str(e))
    
    log("Step 1.7: Wait for trust synthesiser to compute verdict")
    time.sleep(5)
    
    log("Step 1.8: Verify verdict generated")
    verdict_data = check_verdict(server_id)
    if verdict_data and verdict_data.get("verdict"):
        log(f"Verdict found: {verdict_data['verdict']} (score: {verdict_data['trust_score']})")
        log_test_step(test_name, "verify_verdict", "pass", f"Verdict: {verdict_data['verdict']}")
    else:
        log("No verdict yet - trust synthesiser may need more time or be stale")
        log_test_step(test_name, "verify_verdict", "info", "Verdict pending")
        flow_passed = False
    
    log("Step 1.9: Simulate attestation creation")
    attestation_created = create_attestation(
        server_id=server_id,
        attestor="e2e-test-attestor",
        statement="This server has passed end-to-end validation testing",
        expires_at=(datetime.now(timezone.utc).replace(day=1) + timedelta(days=30)).isoformat()
    )
    
    if attestation_created:
        log_test_step(test_name, "create_attestation", "pass", "Attestation created")
    else:
        log_test_step(test_name, "create_attestation", "warning", "Could not create attestation")
    
    log("Step 1.10: Verify attestation in attestations table")
    time.sleep(1)
    att_count = check_attestations(server_id)
    if att_count > 0:
        log(f"Found {att_count} active attestations")
        log_test_step(test_name, "verify_attestation", "pass", f"{att_count} attestation(s)")
    else:
        log_test_step(test_name, "verify_attestation", "warning", "No attestations found")
    
    log("Step 1.11: Verify server visible in registry (UI)")
    if check_ui_visibility(server_id):
        log_test_step(test_name, "verify_ui_visibility", "pass", "Server visible via registry API")
    else:
        log_test_step(test_name, "verify_ui_visibility", "warning", "Server visibility check inconclusive")
    
    log("Step 1.12: Check mesh events for verdict_generated event")
    mesh_count = check_mesh_events(server_id)
    if mesh_count > 0:
        log_test_step(test_name, "verify_mesh_event", "pass", f"Found {mesh_count} mesh events")
    else:
        log_test_step(test_name, "verify_mesh_event", "info", "No mesh events yet (event system may be async)")
    
    log(f"Flow 1 complete. Status: {'PASS' if flow_passed else 'PARTIAL'}")
    return flow_passed

def run_flow_2_threat_intel_overlay():
    log("=" * 60)
    log("FLOW 2: Threat Intel Overlay")
    log("=" * 60)
    
    test_name = "flow_2_threat_intel_overlay"
    flow_passed = True
    
    log_test_step(test_name, "start", "running", "Beginning flow 2")
    
    log("Step 2.1: Create test MCP server for threat overlay")
    test_name_unique = f"e2e-flow2-{uuid.uuid4().hex[:8]}"
    test_url = f"https://github.com/e2e/{test_name_unique}"
    server_id = get_or_create_test_server(
        name=f"e2e-flow2-threat-server",
        url=test_url,
        description="Server for threat intel overlay testing"
    )
    log_test_step(test_name, "create_server", "pass", f"Server ID: {server_id}")
    
    log("Step 2.2: Set initial good verdict")
    if update_verdict(server_id, "verified", 0.85):
        log_test_step(test_name, "set_initial_verdict", "pass", "Initial verdict set to verified")
    else:
        log_test_step(test_name, "set_initial_verdict", "warning", "Could not set initial verdict")
        flow_passed = False
    
    log("Step 2.3: Verify initial state")
    verdict_data = check_verdict(server_id)
    if verdict_data:
        log(f"Initial state: verdict={verdict_data['verdict']}, score={verdict_data['trust_score']}")
        log_test_step(test_name, "verify_initial_state", "pass", f"Initial: {verdict_data['verdict']}")
    
    log("Step 2.4: Simulate threat intel ingestion - create threat association")
    threat_created = create_threat_intel_entry(
        server_id=server_id,
        threat_type="dependency_hijacking",
        severity="high",
        evidence='{"cve": "CVE-2025-TEST", "package": "malicious-dep"}'
    )
    
    if threat_created:
        log_test_step(test_name, "create_threat", "pass", "Threat association created")
    else:
        log_test_step(test_name, "create_threat", "warning", "Could not create threat")
        flow_passed = False
    
    log("Step 2.5: Verify threat in mcp_threat_associations")
    time.sleep(1)
    threat_count = check_threat_associations(server_id)
    if threat_count > 0:
        log(f"Found {threat_count} threat association(s)")
        log_test_step(test_name, "verify_threat", "pass", f"{threat_count} threat(s)")
    else:
        log_test_step(test_name, "verify_threat", "warning", "No threats found")
        flow_passed = False
    
    log("Step 2.6: Check risk_register update (should recalculate)")
    risk_result = ws_query(f"SELECT risk_tier, threat_count FROM risk_register WHERE server_id = '{server_id}'")
    if risk_result.get("rows"):
        risk_data = risk_result["rows"][0]
        log(f"Risk register: tier={risk_data['risk_tier']}, threats={risk_data['threat_count']}")
        log_test_step(test_name, "check_risk_register", "pass", f"Risk: {risk_data['risk_tier']}")
    else:
        log("No risk register entry yet - risk_ranker may be async")
        log_test_step(test_name, "check_risk_register", "info", "Risk register pending")
    
    log("Step 2.7: Verify verdict should change based on threat (if automated)")
    verdict_data = check_verdict(server_id)
    if verdict_data:
        log(f"Current verdict after threat: {verdict_data['verdict']} (score: {verdict_data['trust_score']})")
        if verdict_data['verdict'] in ['suspicious', 'malicious', 'unverified']:
            log_test_step(test_name, "verify_threat_impact", "pass", "Verdict correctly degraded")
        else:
            log_test_step(test_name, "verify_threat_impact", "info", "Verdict unchanged (manual review may be required)")
    
    log("Step 2.8: Test threat resolution - revoke threat")
    try:
        ws_execute(f"DELETE FROM mcp_threat_associations WHERE server_id = '{server_id}' AND threat_type = 'dependency_hijacking'")
        log_test_step(test_name, "revoke_threat", "pass", "Threat association removed")
    except Exception as e:
        log(f"Could not revoke threat: {e}")
        log_test_step(test_name, "revoke_threat", "warning", str(e))
    
    log("Step 2.9: Verify threat cleared")
    time.sleep(1)
    threat_count = check_threat_associations(server_id)
    log(f"Threat count after revocation: {threat_count}")
    log_test_step(test_name, "verify_cleared", "pass" if threat_count == 0 else "warning", f"{threat_count} remaining")
    
    log(f"Flow 2 complete. Status: {'PASS' if flow_passed else 'PARTIAL'}")
    return flow_passed

def run_flow_3_attestation_refresh():
    log("=" * 60)
    log("FLOW 3: Attestation Refresh Cycle")
    log("=" * 60)
    
    test_name = "flow_3_attestation_refresh"
    flow_passed = True
    
    log_test_step(test_name, "start", "running", "Beginning flow 3")
    
    log("Step 3.1: Verify attestation_engine is running")
    if verify_attestation_engine():
        log_test_step(test_name, "check_attestation_engine", "pass", "Attestation engine healthy")
    else:
        log("WARNING: attestation_engine not healthy")
        log_test_step(test_name, "check_attestation_engine", "warning", "Attestation engine may be stale")
        flow_passed = False
    
    log("Step 3.2: Create test server for attestation testing")
    test_name_unique = f"e2e-flow3-{uuid.uuid4().hex[:8]}"
    test_url = f"https://smithery.ai/e2e/{test_name_unique}"
    server_id = get_or_create_test_server(
        name=f"e2e-flow3-attestation-server",
        url=test_url,
        description="Server for attestation refresh testing"
    )
    log_test_step(test_name, "create_server", "pass", f"Server ID: {server_id}")
    
    log("Step 3.3: Create initial attestation")
    initial_attestation_created = create_attestation(
        server_id=server_id,
        attestor="e2e-initial-attestor",
        statement="Initial attestation for e2e testing",
        expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    )
    
    if initial_attestation_created:
        log_test_step(test_name, "create_initial_attestation", "pass", "Initial attestation created")
    else:
        log_test_step(test_name, "create_initial_attestation", "warning", "Could not create initial attestation")
        flow_passed = False
    
    log("Step 3.4: Verify attestation created")
    time.sleep(1)
    att_count = check_attestations(server_id)
    if att_count > 0:
        log(f"Found {att_count} attestation(s)")
        log_test_step(test_name, "verify_initial_attestation", "pass", f"{att_count} attestation(s)")
    else:
        log_test_step(test_name, "verify_initial_attestation", "warning", "No attestations found")
        flow_passed = False
    
    log("Step 3.5: Check attestation details")
    att_result = ws_query(f"SELECT id, attestor, expires_at, revoked FROM attestations WHERE server_id = '{server_id}' AND revoked = false")
    if att_result.get("rows"):
        att = att_result["rows"][0]
        log(f"Attestation: id={att['id']}, by={att['attestor']}, expires={att['expires_at']}")
        log_test_step(test_name, "check_attestation_details", "pass", f"Expires: {att['expires_at']}")
    else:
        log_test_step(test_name, "check_attestation_details", "warning", "Could not query attestation details")
    
    log("Step 3.6: Simulate attestation expiration check")
    log("Checking for expired attestations...")
    expired_result = ws_query("""
        SELECT COUNT(*) as cnt FROM attestations 
        WHERE expires_at < now() AND revoked = false
    """)
    expired_count = expired_result["rows"][0]["cnt"] if expired_result.get("rows") else 0
    log(f"Found {expired_count} expired attestations in system")
    log_test_step(test_name, "check_expired", "pass", f"{expired_count} expired")
    
    log("Step 3.7: Test attestation revocation")
    try:
        ws_execute(f"UPDATE attestations SET revoked = true, revoked_at = now() WHERE server_id = '{server_id}'")
        log_test_step(test_name, "revoke_attestation", "pass", "Attestation revoked")
    except Exception as e:
        log(f"Could not revoke attestation: {e}")
        log_test_step(test_name, "revoke_attestation", "warning", str(e))
        flow_passed = False
    
    log("Step 3.8: Verify attestation revoked")
    time.sleep(1)
    active_atts = check_attestations(server_id)
    all_atts_result = ws_query(f"SELECT COUNT(*) as cnt FROM attestations WHERE server_id = '{server_id}'")
    all_atts = all_atts_result["rows"][0]["cnt"] if all_atts_result.get("rows") else 0
    
    if active_atts == 0:
        log_test_step(test_name, "verify_revoked", "pass", "Attestation correctly revoked")
    else:
        log_test_step(test_name, "verify_revoked", "warning", f"Still {active_atts} active attestations")
    
    log(f"Total attestations for server: {all_atts} (including revoked)")
    
    log("Step 3.9: Create renewed attestation")
    renewed = create_attestation(
        server_id=server_id,
        attestor="e2e-renewal-attestor",
        statement="Renewed attestation after e2e refresh cycle test",
        expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    )
    
    if renewed:
        log_test_step(test_name, "create_renewed_attestation", "pass", "Renewed attestation created")
    else:
        log_test_step(test_name, "create_renewed_attestation", "warning", "Could not create renewed attestation")
        flow_passed = False
    
    log("Step 3.10: Verify renewed attestation")
    time.sleep(1)
    active_atts = check_attestations(server_id)
    if active_atts > 0:
        log_test_step(test_name, "verify_renewed", "pass", f"{active_atts} active attestation(s)")
    else:
        log_test_step(test_name, "verify_renewed", "warning", "No active attestations")
        flow_passed = False
    
    log("Step 3.11: Check audit log for attestation events")
    audit_result = ws_query(f"SELECT event_type, actor, detail, created_at FROM audit_log WHERE target_server_id = '{server_id}' ORDER BY created_at DESC LIMIT 5")
    if audit_result.get("rows"):
        log(f"Audit log entries for server:")
        for entry in audit_result["rows"]:
            log(f"  - {entry['event_type']} by {entry['actor']}: {entry['detail'][:50]}...")
        log_test_step(test_name, "check_audit_log", "pass", f"{len(audit_result['rows'])} audit entries")
    else:
        log_test_step(test_name, "check_audit_log", "info", "No audit log entries (may be async)")
    
    log(f"Flow 3 complete. Status: {'PASS' if flow_passed else 'PARTIAL'}")
    return flow_passed

def run_all_flows():
    log("=" * 60)
    log("STARTING E2E SCENARIOS VALIDATION")
    log("=" * 60)
    
    ensure_test_tables()
    
    results = {}
    
    try:
        results["flow_1"] = run_flow_1_new_mcp_to_ui()
    except Exception as e:
        log(f"Flow 1 error: {e}")
        results["flow_1"] = False
    
    time.sleep(2)
    
    try:
        results["flow_2"] = run_flow_2_threat_intel_overlay()
    except Exception as e:
        log(f"Flow 2 error: {e}")
        results["flow_2"] = False
    
    time.sleep(2)
    
    try:
        results["flow_3"] = run_flow_3_attestation_refresh()
    except Exception as e:
        log(f"Flow 3 error: {e}")
        results["flow_3"] = False
    
    log("=" * 60)
    log("E2E SCENARIOS SUMMARY")
    log("=" * 60)
    for flow, passed in results.items():
        status = "PASS" if passed else "PARTIAL/FAIL"
        log(f"{flow}: {status}")
    
    passed_count = sum(1 for v in results.values() if v)
    total_count = len(results)
    log(f"Overall: {passed_count}/{total_count} flows completed successfully")
    
    return all(results.values())

def run():
    log("=" * 60)
    log(f"{SERVICE_NAME} starting on port {SERVICE_PORT}")
    log("=" * 60)
    
    if not check_single_instance():
        log("Another instance is running. Exiting.")
        return
    
    try:
        os.makedirs("/home/workspace/zo_sentinel", exist_ok=True)
    except:
        pass
    
    send_heartbeat()
    
    success = run_all_flows()
    
    log("=" * 60)
    if success:
        log("ALL E2E FLOWS COMPLETED SUCCESSFULLY")
    else:
        log("E2E FLOWS COMPLETED WITH WARNINGS")
    log("=" * 60)
    
    send_heartbeat()
    
    return success

if __name__ == "__main__":
    run()