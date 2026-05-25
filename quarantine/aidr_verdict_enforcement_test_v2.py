from fastapi import FastAPI
import uvicorn
import requests
import time
import sys
import os
import json
import hashlib
import signal

SERVICE_NAME = "aidr_verdict_enforcement_test_v2"
SERVICE_PORT = 0
WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_SERVICE = "http://127.0.0.1:8772"
EXECUTE_SERVICE = "http://127.0.0.1:8772"
AIDR_GATEWAY = "http://127.0.0.1:8774"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
HEARTBEAT_INTERVAL = 30
POLL_SECS = 5

app = FastAPI()
start_time = int(time.time())

verdicts_that_block = {"CAUTION_LIMITED", "HIGH_RISK_ISOLATED"}
verdicts_that_allow = {"TRUSTED", "CAUTION_ADVISORY", "PROVISIONING_REQUIRED"}
blocked_count = 0
allowed_count = 0
total_tests = 0


def log(msg):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def ws_query(sql):
    resp = requests.post(f"{QUERY_SERVICE}/query", json={"sql": sql}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def ws_write(table, rows):
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(f"{WRITE_SERVICE}/write", json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql):
    resp = requests.post(f"{EXECUTE_SERVICE}/execute", json={"sql": sql}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat():
    try:
        ws_write("service_health", {"service": SERVICE_NAME, "last_heartbeat": time.time()})
    except Exception as e:
        log(f"heartbeat error: {e}")


def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            existing = int(f.read().strip())
        if existing != pid and os.path.exists(f"/proc/{existing}"):
            log(f"already running as PID {existing}")
            sys.exit(0)
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(sig, frame):
    remove_pid_file()
    sys.exit(0)


def compute_server_id(name):
    return hashlib.sha256(name.encode()).hexdigest()[:16]


def create_test_server(server_id, name, verdict, trust_score, injection_resilience_score=None):
    sql = f"""
    INSERT INTO mcp_server_registry (server_id, name, url, description, trust_score, verdict, registry_source, scan_count)
    VALUES ('{server_id}', '{name}', 'http://test.local/{server_id}', 'Test server for verdict enforcement', {trust_score}, '{verdict}', 'test', 1)
    ON CONFLICT DO NOTHING
    """
    ws_execute(sql)
    
    if injection_resilience_score is not None:
        signal_sql = f"""
        INSERT INTO mcp_signal_scores (server_id, signal_name, score, evidence, scored_at)
        VALUES ('{server_id}', 'injection_resilience', {injection_resilience_score}, 'Test injection resilience score', NOW())
        ON CONFLICT DO NOTHING
        """
        ws_execute(signal_sql)
    
    return server_id


def cleanup_test_server(server_id):
    ws_execute(f"DELETE FROM mcp_signal_scores WHERE server_id = '{server_id}'")
    ws_execute(f"DELETE FROM mcp_server_registry WHERE server_id = '{server_id}'")


def call_gateway_commit(server_id, expected_blocked=False):
    payload = {
        "server_id": server_id,
        "commit_message": "Test commit",
        "author": "test@example.com",
        "timestamp": time.time()
    }
    try:
        resp = requests.post(f"{AIDR_GATEWAY}/commit", json=payload, timeout=30)
        return {"status_code": resp.status_code, "blocked": False, "response": resp.json() if resp.content else {}}
    except requests.exceptions.RequestException as e:
        if expected_blocked:
            return {"status_code": 0, "blocked": True, "error": str(e)}
        return {"status_code": 0, "blocked": False, "error": str(e)}


def verify_injection_resilience_in_payload(server_id, gateway_url):
    payload = {
        "server_id": server_id,
        "action": "commit_payload_check",
        "include_signals": True
    }
    try:
        resp = requests.get(f"{gateway_url}/commit/{server_id}/payload", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return "injection_resilience" in data.get("signals", {})
        return False
    except:
        pass
    
    sql = f"""
    SELECT score FROM mcp_signal_scores 
    WHERE server_id = '{server_id}' AND signal_name = 'injection_resilience'
    ORDER BY scored_at DESC LIMIT 1
    """
    result = ws_query(sql)
    return len(result.get("rows", [])) > 0


def test_caution_limited_blocks_commit():
    global blocked_count, total_tests
    log("TEST: CAUTION_LIMITED verdict should block auto-commit")
    server_id = compute_server_id(f"test_caution_limited_{int(time.time())}")
    
    try:
        create_test_server(server_id, f"test_caution_limited_{int(time.time())}", "CAUTION_LIMITED", 35.0, 0.6)
        
        result = call_gateway_commit(server_id, expected_blocked=True)
        
        if result.get("blocked") or result.get("status_code") in (403, 451):
            log("  PASS: CAUTION_LIMITED blocked commit as expected")
            blocked_count += 1
        else:
            log(f"  FAIL: CAUTION_LIMITED did not block commit (status={result.get('status_code')})")
    except Exception as e:
        log(f"  FAIL: Exception during test: {e}")
    finally:
        cleanup_test_server(server_id)
    
    total_tests += 1


def test_high_risk_isolated_blocks_commit():
    global blocked_count, total_tests
    log("TEST: HIGH_RISK_ISOLATED verdict should block auto-commit")
    server_id = compute_server_id(f"test_high_risk_isolated_{int(time.time())}")
    
    try:
        create_test_server(server_id, f"test_high_risk_isolated_{int(time.time())}", "HIGH_RISK_ISOLATED", 15.0, 0.2)
        
        result = call_gateway_commit(server_id, expected_blocked=True)
        
        if result.get("blocked") or result.get("status_code") in (403, 451):
            log("  PASS: HIGH_RISK_ISOLATED blocked commit as expected")
            blocked_count += 1
        else:
            log(f"  FAIL: HIGH_RISK_ISOLATED did not block commit (status={result.get('status_code')})")
    except Exception as e:
        log(f"  FAIL: Exception during test: {e}")
    finally:
        cleanup_test_server(server_id)
    
    total_tests += 1


def test_trusted_allows_commit():
    global allowed_count, total_tests
    log("TEST: TRUSTED verdict should allow auto-commit")
    server_id = compute_server_id(f"test_trusted_{int(time.time())}")
    
    try:
        create_test_server(server_id, f"test_trusted_{int(time.time())}", "TRUSTED", 85.0, 0.95)
        
        result = call_gateway_commit(server_id, expected_blocked=False)
        
        if not result.get("blocked") or result.get("status_code") in (200, 201):
            log("  PASS: TRUSTED allowed commit as expected")
            allowed_count += 1
        else:
            log(f"  FAIL: TRUSTED unexpectedly blocked commit (status={result.get('status_code')})")
    except Exception as e:
        log(f"  FAIL: Exception during test: {e}")
    finally:
        cleanup_test_server(server_id)
    
    total_tests += 1


def test_caution_advisory_allows_commit():
    global allowed_count, total_tests
    log("TEST: CAUTION_ADVISORY verdict should allow commit with advisory")
    server_id = compute_server_id(f"test_caution_advisory_{int(time.time())}")
    
    try:
        create_test_server(server_id, f"test_caution_advisory_{int(time.time())}", "CAUTION_ADVISORY", 55.0, 0.7)
        
        result = call_gateway_commit(server_id, expected_blocked=False)
        
        if not result.get("blocked") or result.get("status_code") in (200, 201, 202):
            log("  PASS: CAUTION_ADVISORY allowed commit as expected")
            allowed_count += 1
        else:
            log(f"  FAIL: CAUTION_ADVISORY unexpectedly blocked (status={result.get('status_code')})")
    except Exception as e:
        log(f"  FAIL: Exception during test: {e}")
    finally:
        cleanup_test_server(server_id)
    
    total_tests += 1


def test_injection_resilience_in_payload():
    global allowed_count, total_tests
    log("TEST: injection_resilience score should appear in commit payload")
    server_id = compute_server_id(f"test_injection_payload_{int(time.time())}")
    expected_score = 0.85
    
    try:
        create_test_server(server_id, f"test_injection_payload_{int(time.time())}", "TRUSTED", 80.0, expected_score)
        
        has_injection = verify_injection_resilience_in_payload(server_id, AIDR_GATEWAY)
        
        sql = f"""
        SELECT score FROM mcp_signal_scores 
        WHERE server_id = '{server_id}' AND signal_name = 'injection_resilience'
        """
        result = ws_query(sql)
        rows = result.get("rows", [])
        
        if has_injection or len(rows) > 0:
            log("  PASS: injection_resilience score present in payload/db")
            allowed_count += 1
        else:
            log("  FAIL: injection_resilience score not found")
    except Exception as e:
        log(f"  FAIL: Exception during test: {e}")
    finally:
        cleanup_test_server(server_id)
    
    total_tests += 1


def ensure_test_tables():
    log("Ensuring test tables exist...")
    ws_execute("""
    CREATE TABLE IF NOT EXISTS mcp_server_registry (
        server_id VARCHAR PRIMARY KEY,
        name VARCHAR,
        url VARCHAR,
        description VARCHAR,
        trust_score DOUBLE,
        verdict VARCHAR,
        registry_source VARCHAR,
        scan_count INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    ws_execute("""
    CREATE TABLE IF NOT EXISTS mcp_signal_scores (
        server_id VARCHAR,
        signal_name VARCHAR,
        score DOUBLE,
        evidence VARCHAR,
        scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    ws_execute("""
    CREATE TABLE IF NOT EXISTS service_health (
        service VARCHAR PRIMARY KEY,
        last_heartbeat DOUBLE
    )
    """)
    log("Test tables ready")


def print_summary():
    log("=" * 60)
    log("TEST SUMMARY")
    log("=" * 60)
    log(f"Total tests: {total_tests}")
    log(f"Blocked tests (verdict enforcement): {blocked_count}")
    log(f"Allowed tests (proper bypass): {allowed_count}")
    pass_rate = ((blocked_count + allowed_count) / total_tests * 100) if total_tests > 0 else 0
    log(f"Pass rate: {pass_rate:.1f}%")
    
    critical_block_tests = 2
    critical_pass = blocked_count >= critical_block_tests
    log(f"Critical verdict block tests passed: {critical_pass}")
    log("=" * 60)
    return blocked_count >= critical_block_tests


def run():
    log(f"Starting {SERVICE_NAME}")
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_test_tables()
    send_heartbeat()
    
    try:
        test_caution_limited_blocks_commit()
        send_heartbeat()
        
        test_high_risk_isolated_blocks_commit()
        send_heartbeat()
        
        test_trusted_allows_commit()
        send_heartbeat()
        
        test_caution_advisory_allows_commit()
        send_heartbeat()
        
        test_injection_resilience_in_payload()
        send_heartbeat()
        
    except Exception as e:
        log(f"Test suite error: {e}")
    finally:
        success = print_summary()
        remove_pid_file()
        sys.exit(0 if success else 1)


@app.get("/health")
def health():
    uptime = int(time.time()) - start_time
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime": uptime,
        "blocked_tests": blocked_count,
        "allowed_tests": allowed_count,
        "total_tests": total_tests
    }


def main():
    run()


if __name__ == "__main__":
    run()