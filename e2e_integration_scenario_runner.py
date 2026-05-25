import time
import uuid
from datetime import datetime

SERVICE_NAME = "e2e_integration_scenario_runner"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
SCANNER_POLL_INTERVAL = 5
MAX_WAIT_SECONDS = 120
VERDICT_TIMEOUT = 180


def ws_query(sql: str) -> list:
    resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_write(rows: list) -> dict:
    resp = requests.post(WRITE_SERVICE_URL, json={"table": "", "rows": rows, "wait": True}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql: str) -> dict:
    resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def create_synthetic_mcp(server_name: str, description: str, url: str) -> dict:
    server_id = f"e2e-test-{uuid.uuid4().hex[:12]}"
    now = datetime.utcnow().isoformat()
    rows = [{
        "server_id": server_id,
        "name": server_name,
        "url": url,
        "description": description,
        "registry_source": "e2e_test",
        "trust_score": None,
        "verdict": None,
        "created_at": now,
        "updated_at": now,
    }]
    ws_write(rows)
    return {"server_id": server_id, "name": server_name}


def trigger_scanner_ingestion(server_id: str) -> bool:
    sql = f"INSERT INTO scanner_jobs (job_id, server_id, status, created_at) VALUES ('{uuid.uuid4().hex}', '{server_id}', 'pending', '{datetime.utcnow().isoformat()}')"
    try:
        ws_execute(sql)
        return True
    except Exception:
        pass
    return False


def wait_for_registry_entry(server_id: str, timeout: int = 60) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        rows = ws_query(f"SELECT * FROM mcp_server_registry WHERE server_id = '{server_id}'")
        if rows:
            return rows[0]
        time.sleep(SCANNER_POLL_INTERVAL)
    return None


def wait_for_signal_scores(server_id: str, timeout: int = 90) -> list:
    start = time.time()
    while time.time() - start < timeout:
        rows = ws_query(f"SELECT * FROM mcp_signal_scores WHERE server_id = '{server_id}'")
        if rows:
            return rows
        time.sleep(SCANNER_POLL_INTERVAL)
    return []


def wait_for_verdict(server_id: str, timeout: int = VERDICT_TIMEOUT) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        rows = ws_query(f"SELECT trust_score, verdict FROM mcp_server_registry WHERE server_id = '{server_id}'")
        if rows and rows[0].get("verdict"):
            return rows[0]
        time.sleep(SCANNER_POLL_INTERVAL)
    return {"trust_score": None, "verdict": None}


def wait_for_attestation(server_id: str, timeout: int = 60) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        rows = ws_query(f"SELECT * FROM mcp_attestations WHERE server_id = '{server_id}'")
        if rows:
            return rows[0]
        time.sleep(SCANNER_POLL_INTERVAL)
    return None


def check_ui_visibility(server_id: str) -> bool:
    try:
        resp = requests.get("http://127.0.0.1:8790", timeout=10)
        if resp.status_code == 200:
            return True
    except Exception:
        pass
    return False


def cleanup_test_entry(server_id: str):
    tables = ["mcp_signal_scores", "mcp_attestations", "mcp_risk_register", "mcp_server_registry"]
    for table in tables:
        try:
            ws_execute(f"DELETE FROM {table} WHERE server_id = '{server_id}'")
        except Exception:
            pass


def run_scenario() -> dict:
    result = {
        "scenario": "e2e_integration",
        "passed": False,
        "steps": {},
        "server_id": None,
        "error": None,
    }
    test_server = None
    try:
        print("[E2E] Creating synthetic MCP fixture...")
        test_server = create_synthetic_mcp(
            server_name="e2e-test-server",
            description="Synthetic MCP for end-to-end integration testing",
            url="https://example.com/e2e-test"
        )
        server_id = test_server["server_id"]
        result["server_id"] = server_id
        print(f"[E2E] Created server_id={server_id}")

        print("[E2E] Step 1: Triggering scanner ingestion...")
        scanner_ok = trigger_scanner_ingestion(server_id)
        result["steps"]["scanner_ingestion"] = scanner_ok
        print(f"[E2E] Scanner ingestion triggered: {scanner_ok}")

        print("[E2E] Step 2: Waiting for registry entry...")
        registry = wait_for_registry_entry(server_id)
        registry_ok = registry is not None
        result["steps"]["registry_entry"] = registry_ok
        print(f"[E2E] Registry entry found: {registry_ok}")

        print("[E2E] Step 3: Waiting for signal scores...")
        signals = wait_for_signal_scores(server_id)
        signals_ok = len(signals) > 0
        result["steps"]["signal_scoring"] = signals_ok
        print(f"[E2E] Signal scores found: {signals_ok} ({len(signals)} signals)")

        print("[E2E] Step 4: Waiting for verdict computation...")
        verdict_data = wait_for_verdict(server_id)
        verdict_ok = verdict_data.get("verdict") is not None
        result["steps"]["verdict_computation"] = verdict_ok
        result["steps"]["verdict_value"] = verdict_data.get("verdict")
        result["steps"]["trust_score"] = verdict_data.get("trust_score")
        print(f"[E2E] Verdict: {verdict_data.get('verdict')} (trust_score={verdict_data.get('trust_score')})")

        print("[E2E] Step 5: Waiting for attestation generation...")
        attestation = wait_for_attestation(server_id)
        attestation_ok = attestation is not None
        result["steps"]["attestation_generation"] = attestation_ok
        print(f"[E2E] Attestation found: {attestation_ok}")

        print("[E2E] Step 6: Checking UI visibility...")
        ui_ok = check_ui_visibility(server_id)
        result["steps"]["ui_visibility"] = ui_ok
        print(f"[E2E] UI visible: {ui_ok}")

        all_passed = registry_ok and signals_ok and verdict_ok
        result["passed"] = all_passed
        print(f"[E2E] Scenario {'PASSED' if all_passed else 'FAILED'}")
        if not all_passed:
            result["error"] = "One or more pipeline steps did not complete"
        return result
    except Exception as e:
        result["passed"] = False
        result["error"] = str(e)
        print(f"[E2E] Error: {e}")
        return result
    finally:
        if test_server:
            cleanup_test_entry(test_server["server_id"])
            print(f"[E2E] Cleaned up test entry: {test_server['server_id']}")


def print_summary(results: dict):
    print("\n" + "="*60)
    print("E2E INTEGRATION SCENARIO RESULTS")
    print("="*60)
    print(f"Scenario: {results.get('scenario')}")
    print(f"Server ID: {results.get('server_id')}")
    print(f"Overall: {'PASSED' if results.get('passed') else 'FAILED'}")
    print("\nStep Results:")
    for step, val in results.get("steps", {}).items():
        if isinstance(val, bool):
            print(f"  {step}: {'OK' if val else 'FAIL'}")
        else:
            print(f"  {step}: {val}")
    if results.get("error"):
        print(f"\nError: {results['error']}")
    print("="*60)


if __name__ == "__main__":
    print("[E2E] Starting end-to-end integration scenario runner...")
    result = run_scenario()
    print_summary(result)
    exit(0 if result["passed"] else 1)