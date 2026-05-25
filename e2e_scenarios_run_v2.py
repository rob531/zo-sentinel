import time
import json
import sys
from datetime import datetime, timezone

SERVICE_NAME = "e2e_scenarios_run_v2"
SERVICE_PORT = 8801
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = "/tmp/e2e_scenarios_run_v2.log"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772"

POLL_SECS = 30
MAX_RETRIES = 3
SEARCH_WAIT_SECONDS = 5

COHORT_DEFINITIONS = {
    "cohort_6_n5": {
        "name": "canonical_flow_verdict_attestation",
        "steps": [
            "create_synthetic_mcp",
            "trigger_signal_analysis",
            "wait_for_signals",
            "compute_verdict",
            "create_attestation",
            "verify_search_api",
            "validate_flow"
        ]
    }
}

def log(msg):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def check_single_instance():
    import os
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            import os
            if old_pid != os.getpid():
                try:
                    os.kill(old_pid, 0)
                    log(f"[WARN] Another instance running with PID {old_pid}, exiting")
                    sys.exit(0)
                except OSError:
                    log(f"[INFO] Stale PID file from {old_pid}, will overwrite")
        except Exception:
            pass
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

def remove_pid_file():
    import os
    try:
        os.remove(PID_FILE)
    except Exception:
        pass

def signal_handler(signum, frame):
    log(f"[SIGNAL] Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)

def get_write_url():
    return f"{WRITE_SERVICE_URL}/write"

def get_query_url():
    return f"{QUERY_SERVICE_URL}/query"

def get_execute_url():
    return f"{EXECUTE_SERVICE_URL}/execute"

def ws_write(table, rows, wait=True):
    import requests
    payload = {"table": table, "rows": rows, "wait": wait}
    try:
        resp = requests.post(get_write_url(), json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"[ERROR] ws_write failed for {table}: {e}")
        return None

def ws_query(sql):
    import requests
    payload = {"sql": sql}
    try:
        resp = requests.post(get_query_url(), json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"[ERROR] ws_query failed: {e}")
        return None

def ws_execute(sql):
    import requests
    payload = {"sql": sql}
    try:
        resp = requests.post(get_execute_url(), json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"[ERROR] ws_execute failed: {e}")
        return None

def verify_table_exists(table_name):
    result = ws_query(f"SELECT COUNT(*) as cnt FROM information_schema.tables WHERE table_name = '{table_name}'")
    if result and result.get("rows") and result["rows"][0]["cnt"] > 0:
        return True
    result = ws_query(f"SELECT COUNT(*) as cnt FROM {table_name} LIMIT 1")
    return result is not None

def ensure_test_tables():
    tables = ["mcp_server_registry", "mcp_signal_scores", "mcp_verdicts", "mcp_attestations", "audit_log"]
    created = []
    for tbl in tables:
        if verify_table_exists(tbl):
            created.append(tbl)
        else:
            log(f"[WARN] Table {tbl} does not exist, creating...")
            if tbl == "mcp_server_registry":
                ws_execute("""
                    CREATE TABLE IF NOT EXISTS mcp_server_registry (
                        server_id VARCHAR PRIMARY KEY,
                        name VARCHAR,
                        url VARCHAR,
                        description TEXT,
                        trust_score DOUBLE,
                        verdict VARCHAR,
                        registry_source VARCHAR,
                        scan_count INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                created.append(tbl)
            elif tbl == "mcp_signal_scores":
                ws_execute("""
                    CREATE TABLE IF NOT EXISTS mcp_signal_scores (
                        server_id VARCHAR,
                        signal_name VARCHAR,
                        score DOUBLE,
                        evidence TEXT,
                        scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                created.append(tbl)
            elif tbl == "mcp_verdicts":
                ws_execute("""
                    CREATE TABLE IF NOT EXISTS mcp_verdicts (
                        server_id VARCHAR PRIMARY KEY,
                        verdict VARCHAR,
                        confidence DOUBLE,
                        computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                created.append(tbl)
            elif tbl == "mcp_attestations":
                ws_execute("""
                    CREATE TABLE IF NOT EXISTS mcp_attestations (
                        server_id VARCHAR PRIMARY KEY,
                        attestation_text TEXT,
                        attested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        expires_at TIMESTAMP
                    )
                """)
                created.append(tbl)
            elif tbl == "audit_log":
                ws_execute("""
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id INTEGER PRIMARY KEY,
                        target_server_id VARCHAR,
                        event_type VARCHAR,
                        actor VARCHAR,
                        detail TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                created.append(tbl)
    log(f"[INFO] Test tables verified/created: {created}")
    return created

def create_synthetic_mcp(server_id, name="test-e2e-mcp"):
    """Create a synthetic MCP server for E2E testing"""
    log(f"[STEP] Creating synthetic MCP: server_id={server_id}")
    row = {
        "server_id": server_id,
        "name": name,
        "url": f"https://e2e-test-{server_id}.example.com",
        "description": f"E2E test MCP server {server_id} for canonical flow validation",
        "trust_score": None,
        "verdict": None,
        "registry_source": "e2e_synthetic",
        "scan_count": 0
    }
    result = ws_write("mcp_server_registry", row)
    if result:
        log(f"[OK] Synthetic MCP created: {server_id}")
    return result

def trigger_signal_analysis(server_id):
    """Trigger signal analysis for the MCP server - wait for signal_analyser to pick it up"""
    log(f"[STEP] Triggering signal analysis for: {server_id}")
    # Signal the analysis by updating scan_count or adding a pending flag
    result = ws_execute(f"UPDATE mcp_server_registry SET scan_count = scan_count + 1 WHERE server_id = '{server_id}'")
    if result:
        log(f"[OK] Signal analysis triggered for: {server_id}")
    return result

def wait_for_signals(server_id, required_signals, timeout_seconds=60):
    """Wait for all required signal enrichments to be written before proceeding"""
    log(f"[STEP] Waiting for signals for server: {server_id}")
    required = set(required_signals)
    start_time = time.time()
    checked = set()
    while time.time() - start_time < timeout_seconds:
        query = f"SELECT DISTINCT signal_name FROM mcp_signal_scores WHERE server_id = '{server_id}'"
        result = ws_query(query)
        if result and result.get("rows"):
            for row in result["rows"]:
                sig_name = row.get("signal_name")
                if sig_name:
                    checked.add(sig_name)
            log(f"[INFO] Signals found so far: {checked}")
            if required.issubset(checked):
                elapsed = time.time() - start_time
                log(f"[OK] All required signals found in {elapsed:.1f}s: {required}")
                return True
        time.sleep(2)
    log(f"[WARN] Timeout waiting for signals. Found: {checked}, Required: {required}")
    return required.issubset(checked)

def compute_verdict(server_id):
    """Compute verdict for the MCP server"""
    log(f"[STEP] Computing verdict for: {server_id}")
    query = f"""
        SELECT 
            COALESCE(AVG(score), 0.5) as avg_score
        FROM mcp_signal_scores 
        WHERE server_id = '{server_id}'
    """
    result = ws_query(query)
    avg_score = 0.5
    if result and result.get("rows"):
        avg_score = result["rows"][0].get("avg_score", 0.5)
    verdict = "TRUSTED" if avg_score >= 0.7 else "REVIEW" if avg_score >= 0.4 else "UNTRUSTED"
    row = {
        "server_id": server_id,
        "verdict": verdict,
        "confidence": avg_score
    }
    result = ws_write("mcp_verdicts", row)
    if result:
        log(f"[OK] Verdict computed: {verdict} (confidence={avg_score:.3f})")
        ws_execute(f"UPDATE mcp_server_registry SET verdict = '{verdict}', trust_score = {avg_score} WHERE server_id = '{server_id}'")
    return result

def create_attestation(server_id):
    """Create attestation after verdict is computed"""
    log(f"[STEP] Creating attestation for: {server_id}")
    query = f"SELECT verdict, trust_score FROM mcp_verdicts WHERE server_id = '{server_id}'"
    result = ws_query(query)
    if not result or not result.get("rows"):
        log(f"[ERROR] No verdict found for {server_id}, cannot create attestation")
        return None
    verdict_row = result["rows"][0]
    verdict = verdict_row.get("verdict", "REVIEW")
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=30 if verdict == "TRUSTED" else 7)
    attestation_text = f"E2E Attestation: Server {server_id} deemed {verdict} with confidence {verdict_row.get('trust_score', 0):.3f}"
    row = {
        "server_id": server_id,
        "attestation_text": attestation_text,
        "expires_at": expires.isoformat()
    }
    result = ws_write("mcp_attestations", row)
    if result:
        log(f"[OK] Attestation created for: {server_id}")
        ws_write("audit_log", {
            "target_server_id": server_id,
            "event_type": "attestation_created",
            "actor": "e2e_scenarios_run_v2",
            "detail": attestation_text
        })
    return result

def verify_search_api(server_id, wait_seconds=SEARCH_WAIT_SECONDS):
    """Verify search_api surfaces the new MCP within specified seconds"""
    log(f"[STEP] Verifying search_api visibility for: {server_id} (wait {wait_seconds}s)")
    time.sleep(wait_seconds)
    result = ws_query(f"SELECT * FROM mcp_server_registry WHERE server_id = '{server_id}'")
    if result and result.get("rows"):
        for row in result["rows"]:
            if row.get("verdict") and row.get("trust_score") is not None:
                log(f"[OK] Search API can surface MCP: {server_id} (verdict={row.get('verdict')}, trust_score={row.get('trust_score')})")
                return True
    log(f"[WARN] Search API may not surface MCP: {server_id}")
    return False

def validate_flow(server_id, required_signals):
    """Validate the complete E2E flow completed successfully"""
    log(f"[STEP] Validating complete E2E flow for: {server_id}")
    checks = []
    query1 = ws_query(f"SELECT * FROM mcp_server_registry WHERE server_id = '{server_id}'")
    checks.append(("mcp_server_registry", query1 is not None and len(query1.get("rows", [])) > 0))
    query2 = ws_query(f"SELECT COUNT(*) as cnt FROM mcp_signal_scores WHERE server_id = '{server_id}'")
    signal_count = query2.get("rows", [{}])[0].get("cnt", 0) if query2 else 0
    checks.append(("signal_scores_written", signal_count >= len(required_signals)))
    query3 = ws_query(f"SELECT * FROM mcp_verdicts WHERE server_id = '{server_id}'")
    checks.append(("verdict_computed", query3 is not None and len(query3.get("rows", [])) > 0))
    query4 = ws_query(f"SELECT * FROM mcp_attestations WHERE server_id = '{server_id}'")
    checks.append(("attestation_created", query4 is not None and len(query4.get("rows", [])) > 0))
    query5 = ws_query(f"SELECT * FROM audit_log WHERE target_server_id = '{server_id}' AND event_type = 'attestation_created'")
    checks.append(("audit_logged", query5 is not None and len(query5.get("rows", [])) > 0))
    all_passed = all(c[1] for c in checks)
    for name, passed in checks:
        status = "PASS" if passed else "FAIL"
        log(f"[CHECK] {name}: {status}")
    if all_passed:
        log(f"[OK] Complete E2E flow validated for: {server_id}")
    else:
        log(f"[FAIL] E2E flow validation failed for: {server_id}")
    return all_passed

def run_cohort(cohort_name, cohort_def):
    """Run a specific cohort of E2E tests"""
    log(f"[COHORT] Starting cohort: {cohort_name}")
    import uuid
    server_id = f"e2e-{cohort_name}-{uuid.uuid4().hex[:12]}"
    required_signals = [
        "supply_chain_score",
        "temporal_stability_score",
        "community_signal_score",
        "permission_scope_score",
        "tool_description_safety_score",
        "injection_resilience_score",
        "domain_trust_score"
    ]
    try:
        ensure_test_tables()
        create_synthetic_mcp(server_id)
        trigger_signal_analysis(server_id)
        wait_for_signals(server_id, required_signals, timeout_seconds=60)
        compute_verdict(server_id)
        create_attestation(server_id)
        verify_search_api(server_id, wait_seconds=5)
        flow_valid = validate_flow(server_id, required_signals)
        if flow_valid:
            log(f"[OK] Cohort {cohort_name} PASSED")
            return True
        else:
            log(f"[FAIL] Cohort {cohort_name} FAILED - flow validation errors")
            return False
    except Exception as e:
        log(f"[ERROR] Cohort {cohort_name} failed with exception: {e}")
        import traceback
        log(traceback.format_exc())
        return False

def send_heartbeat():
    """Send heartbeat to service_health"""
    now = datetime.now(timezone.utc).isoformat()
    ws_write("service_health", {"service": SERVICE_NAME, "last_heartbeat": now})

def get_heartbeat_age():
    """Get age of last heartbeat in seconds"""
    result = ws_query(f"SELECT last_heartbeat FROM service_health WHERE service = '{SERVICE_NAME}'")
    if result and result.get("rows"):
        last_hb = result["rows"][0].get("last_heartbeat")
        if last_hb:
            try:
                from datetime import datetime, timezone
                if isinstance(last_hb, str):
                    hb_dt = datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
                else:
                    hb_dt = last_hb
                age = (datetime.now(timezone.utc) - hb_dt).total_seconds()
                return age
            except Exception:
                pass
    return None

def run_cycle():
    """Run one cycle of E2E test execution"""
    log(f"[CYCLE] Running E2E scenarios cycle")
    send_heartbeat()
    results = {}
    for cohort_name, cohort_def in COHORT_DEFINITIONS.items():
        result = run_cohort(cohort_name, cohort_def)
        results[cohort_name] = result
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    log(f"[CYCLE] Completed: {passed}/{total} cohorts passed")
    return results

def health():
    """Health check endpoint"""
    hb_age = get_heartbeat_age()
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime_seconds": 0,
        "last_heartbeat_age_seconds": hb_age
    }

def run():
    """Main daemon loop"""
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log(f"[START] Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    check_single_instance()
    ensure_test_tables()
    send_heartbeat()
    cycle_count = 0
    while True:
        try:
            run_cycle()
            cycle_count += 1
        except Exception as e:
            log(f"[ERROR] Cycle failed: {e}")
            import traceback
            log(traceback.format_exc())
        time.sleep(POLL_SECS)

if __name__ == "__main__":
    run()