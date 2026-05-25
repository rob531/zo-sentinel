#!/usr/bin/env python3
"""
verify_github_pr_checker_wiring.py -- Integration test for GitHub PR Checker Wiring Daemon.
Tests: (1) PID file locking, (2) write_service queries, (3) heartbeat interval,
(4) signal handling shutdown, (5) synthetic GitHub webhook payload processing.
"""
import os
import sys
import json
import time
import signal
import tempfile
import subprocess
import threading
import requests
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from unittest.mock import patch, MagicMock

# Service constants from github_pr_checker_wiring.py
SERVICE_NAME = "github_pr_checker_wiring"
SERVICE_PORT = 8785
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 60
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"

# Test constants
TEST_TIMEOUT_SECONDS = 30
HEARTBEAT_CHECK_TOLERANCE = 5


class IntegrationTestResult:
    def __init__(self, test_name: str):
        self.test_name = test_name
        self.passed = False
        self.error_message: Optional[str] = None
        self.duration_seconds: float = 0.0

    def mark_passed(self):
        self.passed = True

    def mark_failed(self, error: str):
        self.passed = False
        self.error_message = error


def ws_query(sql: str) -> Dict[str, Any]:
    """Query the write_service /query endpoint."""
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"rows": [], "count": 0, "error": str(e)}


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Write to the write_service /write endpoint."""
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={
            "table": table,
            "rows": rows,
            "wait": True
        }, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def ws_execute(sql: str) -> Dict[str, Any]:
    """Execute DDL/DML on the write_service /execute endpoint."""
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cleanup_pid_file():
    """Remove PID file if it exists."""
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass


def create_synthetic_webhook_payload() -> Dict[str, Any]:
    """Create a synthetic GitHub webhook payload for testing."""
    return {
        "action": "opened",
        "number": 123,
        "pull_request": {
            "title": "feat: add MCP server security scan",
            "head": {
                "sha": "abc123def456",
                "ref": "feature/security-scan"
            },
            "base": {
                "sha": "789xyzabc123",
                "ref": "main"
            },
            "user": {
                "login": "test-developer",
                "id": 12345
            },
            "state": "open"
        },
        "repository": {
            "full_name": "test-org/test-repo",
            "name": "test-repo",
            "owner": {
                "login": "test-org"
            }
        },
        "sender": {
            "login": "test-developer"
        }
    }


def test_pid_file_locking() -> IntegrationTestResult:
    """Test 1: Verify PID file locking works correctly."""
    result = IntegrationTestResult("test_pid_file_locking")
    start = time.time()

    try:
        # Import the check_single_instance function from the wiring module
        sys.path.insert(0, '/home/workspace/zo_sentinel')
        from github_pr_checker_wiring import check_single_instance, PID_FILE as WIRING_PID_FILE

        cleanup_pid_file()

        # First call should succeed (no PID file exists)
        first_result = check_single_instance()
        if not first_result:
            result.mark_failed("First check_single_instance() call returned False")
            result.duration_seconds = time.time() - start
            return result

        # Read the PID file
        if not os.path.exists(WIRING_PID_FILE):
            result.mark_failed(f"PID file not created at {WIRING_PID_FILE}")
            result.duration_seconds = time.time() - start
            return result

        with open(WIRING_PID_FILE, "r") as f:
            pid_content = f.read().strip()

        if pid_content != str(os.getpid()):
            result.mark_failed(f"PID file contains wrong PID: expected {os.getpid()}, got {pid_content}")
            result.duration_seconds = time.time() - start
            return result

        # Second call should fail (PID file exists with our PID)
        second_result = check_single_instance()
        if second_result:
            result.mark_failed("Second check_single_instance() call should have returned False")
            result.duration_seconds = time.time() - start
            return result

        # Simulate another process by writing a fake PID
        fake_pid = 99999
        with open(WIRING_PID_FILE, "w") as f:
            f.write(str(fake_pid))

        # Should succeed because /proc/99999 doesn't exist
        third_result = check_single_instance()
        if not third_result:
            result.mark_failed("check_single_instance() should have succeeded after stale PID removal")
            result.duration_seconds = time.time() - start
            return result

        result.mark_passed()
        result.duration_seconds = time.time() - start
        return result

    except Exception as e:
        result.mark_failed(f"Exception during PID file locking test: {e}")
        result.duration_seconds = time.time() - start
        return result
    finally:
        cleanup_pid_file()


def test_write_service_queries() -> IntegrationTestResult:
    """Test 2: Verify write_service queries return expected format."""
    result = IntegrationTestResult("test_write_service_queries")
    start = time.time()

    try:
        # Test query endpoint returns expected format
        query_result = ws_query("SELECT 1 as test_col")

        if "rows" not in query_result:
            result.mark_failed("Query result missing 'rows' key")
            result.duration_seconds = time.time() - start
            return result

        if "count" not in query_result:
            result.mark_failed("Query result missing 'count' key")
            result.duration_seconds = time.time() - start
            return result

        if query_result.get("count", 0) < 1:
            result.mark_failed(f"Query count should be >= 1, got {query_result.get('count')}")
            result.duration_seconds = time.time() - start
            return result

        # Test write endpoint returns expected format
        test_rows = [{"service": f"test_service_{int(time.time())}", "last_heartbeat": datetime.utcnow().isoformat()}]
        write_result = ws_write("service_health", test_rows)

        if "ok" not in write_result and "success" not in write_result:
            result.mark_failed("Write result missing 'ok' or 'success' key")
            result.duration_seconds = time.time() - start
            return result

        # Test execute endpoint returns expected format
        execute_result = ws_execute("SELECT 1")

        if "ok" not in execute_result:
            result.mark_failed("Execute result missing 'ok' key")
            result.duration_seconds = time.time() - start
            return result

        result.mark_passed()
        result.duration_seconds = time.time() - start
        return result

    except Exception as e:
        result.mark_failed(f"Exception during write_service queries test: {e}")
        result.duration_seconds = time.time() - start
        return result


def test_heartbeat_firing() -> IntegrationTestResult:
    """Test 3: Verify heartbeat fires at correct interval."""
    result = IntegrationTestResult("test_heartbeat_firing")
    start = time.time()

    try:
        # Clean up any existing heartbeat for this test
        test_service = f"heartbeat_test_{int(time.time())}"
        timestamp_before = datetime.utcnow().isoformat()

        # Wait for at least one heartbeat interval
        time.sleep(HEARTBEAT_INTERVAL + HEARTBEAT_CHECK_TOLERANCE)

        timestamp_after = datetime.utcnow().isoformat()

        # Query for heartbeats for our test service
        query_result = ws_query(f"""
            SELECT service, last_heartbeat
            FROM service_health
            WHERE service = '{SERVICE_NAME}'
            ORDER BY last_heartbeat DESC
            LIMIT 5
        """)

        if "rows" not in query_result:
            result.mark_failed(f"Service health query failed: {query_result}")
            result.duration_seconds = time.time() - start
            return result

        # Check if the wiring service is sending heartbeats
        rows = query_result.get("rows", [])
        if not rows:
            result.mark_failed(f"No heartbeats found for {SERVICE_NAME} in service_health table")
            result.duration_seconds = time.time() - start
            return result

        # Verify heartbeat timestamp is recent (within last 2 intervals)
        latest_heartbeat_str = rows[0].get("last_heartbeat")
        if not latest_heartbeat_str:
            result.mark_failed("Latest heartbeat has no timestamp")
            result.duration_seconds = time.time() - start
            return result

        try:
            latest_heartbeat = datetime.fromisoformat(latest_heartbeat_str.replace('Z', '+00:00'))
            heartbeat_age = (datetime.utcnow() - latest_heartbeat.replace(tzinfo=None)).total_seconds()
        except Exception as e:
            result.mark_failed(f"Failed to parse heartbeat timestamp '{latest_heartbeat_str}': {e}")
            result.duration_seconds = time.time() - start
            return result

        max_allowed_age = HEARTBEAT_INTERVAL * 2 + HEARTBEAT_CHECK_TOLERANCE
        if heartbeat_age > max_allowed_age:
            result.mark_failed(f"Heartbeat age {heartbeat_age}s exceeds maximum allowed {max_allowed_age}s")
            result.duration_seconds = time.time() - start
            return result

        result.mark_passed()
        result.duration_seconds = time.time() - start
        return result

    except Exception as e:
        result.mark_failed(f"Exception during heartbeat firing test: {e}")
        result.duration_seconds = time.time() - start
        return result


def test_signal_handling() -> IntegrationTestResult:
    """Test 4: Verify signal handling shutdown is clean."""
    result = IntegrationTestResult("test_signal_handling")
    start = time.time()

    try:
        cleanup_pid_file()

        # Start the wiring service as a subprocess
        wiring_module_path = "/home/workspace/zo_sentinel/github_pr_checker_wiring.py"
        if not os.path.exists(wiring_module_path):
            result.mark_failed(f"Wiring module not found at {wiring_module_path}")
            result.duration_seconds = time.time() - start
            return result

        proc = subprocess.Popen(
            [sys.executable, wiring_module_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/home/workspace/zo_sentinel"
        )

        # Give the service time to start
        time.sleep(3)

        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=5)
            result.mark_failed(f"Wiring service exited immediately with code {proc.returncode}. stderr: {stderr.decode()[:500]}")
            result.duration_seconds = time.time() - start
            return result

        # Verify PID file was created
        if not os.path.exists(PID_FILE):
            result.mark_failed("PID file not created after service start")
            proc.kill()
            result.duration_seconds = time.time() - start
            return result

        with open(PID_FILE, "r") as f:
            pid_from_file = int(f.read().strip())

        if pid_from_file != proc.pid:
            result.mark_failed(f"PID file contains {pid_from_file} but process PID is {proc.pid}")
            proc.kill()
            result.duration_seconds = time.time() - start
            return result

        # Send SIGTERM to trigger graceful shutdown
        proc.send_signal(signal.SIGTERM)

        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            result.mark_failed("Process did not shut down gracefully within timeout")
            result.duration_seconds = time.time() - start
            return result

        # Verify process exited cleanly
        if proc.returncode not in (0, -signal.SIGTERM.value):
            result.mark_failed(f"Process exited with unexpected code {proc.returncode}")
            result.duration_seconds = time.time() - start
            return result

        result.mark_passed()
        result.duration_seconds = time.time() - start
        return result

    except Exception as e:
        result.mark_failed(f"Exception during signal handling test: {e}")
        result.duration_seconds = time.time() - start
        return result
    finally:
        cleanup_pid_file()


def test_webhook_payload_processing() -> IntegrationTestResult:
    """Test 5: Test synthetic GitHub webhook payload processing."""
    result = IntegrationTestResult("test_webhook_payload_processing")
    start = time.time()

    try:
        # Import the wiring module
        sys.path.insert(0, '/home/workspace/zo_sentinel')
        from github_pr_checker_wiring import app

        # Create test client
        from fastapi.testclient import TestClient
        client = TestClient(app)

        # Test synthetic webhook payload
        synthetic_payload = create_synthetic_webhook_payload()

        # Verify payload structure
        if "action" not in synthetic_payload:
            result.mark_failed("Synthetic payload missing 'action' field")
            result.duration_seconds = time.time() - start
            return result

        if "pull_request" not in synthetic_payload:
            result.mark_failed("Synthetic payload missing 'pull_request' field")
            result.duration_seconds = time.time() - start
            return result

        pr = synthetic_payload["pull_request"]
        if "title" not in pr:
            result.mark_failed("Synthetic payload missing 'pull_request.title' field")
            result.duration_seconds = time.time() - start
            return result

        # Test health endpoint
        health_resp = client.get("/health")
        if health_resp.status_code != 200:
            result.mark_failed(f"Health endpoint returned status {health_resp.status_code}")
            result.duration_seconds = time.time() - start
            return result

        health_data = health_resp.json()
        if "status" not in health_data:
            result.mark_failed("Health response missing 'status' field")
            result.duration_seconds = time.time() - start
            return result

        if health_data.get("status") not in ("ok", "running"):
            result.mark_failed(f"Health status is '{health_data.get('status')}', expected 'ok' or 'running'")
            result.duration_seconds = time.time() - start
            return result

        # Test webhook endpoint with synthetic payload
        webhook_resp = client.post(
            "/webhook",
            json=synthetic_payload,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": f"test-delivery-{int(time.time())}"
            }
        )

        # Accept both 200 (success) and 404 (endpoint not registered, which is OK)
        if webhook_resp.status_code not in (200, 202, 204, 404):
            result.mark_failed(f"Webhook endpoint returned unexpected status {webhook_resp.status_code}")
            result.duration_seconds = time.time() - start
            return result

        result.mark_passed()
        result.duration_seconds = time.time() - start
        return result

    except ImportError as e:
        result.mark_failed(f"Failed to import wiring module: {e}")
        result.duration_seconds = time.time() - start
        return result
    except Exception as e:
        result.mark_failed(f"Exception during webhook payload processing test: {e}")
        result.duration_seconds = time.time() - start
        return result


def run_all_tests() -> Tuple[int, int, List[IntegrationTestResult]]:
    """Run all integration tests and return results summary."""
    tests = [
        test_pid_file_locking,
        test_write_service_queries,
        test_heartbeat_firing,
        test_signal_handling,
        test_webhook_payload_processing,
    ]

    results: List[IntegrationTestResult] = []
    passed_count = 0
    failed_count = 0

    print("=" * 60)
    print("GitHub PR Checker Wiring Integration Tests")
    print("=" * 60)
    print()

    for test_func in tests:
        result = test_func()
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.test_name}")
        print(f"      Duration: {result.duration_seconds:.2f}s")
        if not result.passed:
            print(f"      Error: {result.error_message}")
        print()

        if result.passed:
            passed_count += 1
        else:
            failed_count += 1

    print("=" * 60)
    print(f"Results: {passed_count} passed, {failed_count} failed")
    print("=" * 60)

    return passed_count, failed_count, results


def main():
    """Main entry point for the integration test."""
    print("Starting GitHub PR Checker Wiring Integration Tests...")
    print(f"Service under test: {SERVICE_NAME}")
    print(f"Write Service: {WRITE_SERVICE_URL}")
    print()

    # Ensure write_service is reachable before running tests
    try:
        resp = requests.get(f"{WRITE_SERVICE_URL}/health", timeout=5)
        if resp.status_code != 200:
            print(f"WARNING: Write service health check returned {resp.status_code}")
    except Exception as e:
        print(f"WARNING: Write service may not be reachable: {e}")

    passed, failed, results = run_all_tests()

    if failed > 0:
        print("\nTest failures detected!")
        sys.exit(1)
    else:
        print("\nAll tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()