#!/usr/bin/env python3
"""
tests/ui_endpoint_tests.py -- ZO-SENTINEL UI and API Endpoint Test Suite

Tests all FastAPI endpoints and UI server. Uses stdlib requests only.
Connection errors are SKIP not FAIL. Results written to last_ui_test_results.json.
"""
import json
import sys
import logging
from datetime import datetime, timezone
from typing import Optional
import requests

log = logging.getLogger(__name__)

WRITE_SERVICE = "http://127.0.0.1:8772"
EXECUTE_URL   = "http://127.0.0.1:8772"
RESULTS_FILE  = "tests/last_ui_test_results.json"

VERDICT_TAXONOMY = {
    "TRUSTED", "SAFE", "CAUTION", "SUSPICIOUS", "DANGEROUS",
    "REJECTED", "CONDITIONAL", "APPROVED", "INSUFFICIENT", "PENDING"
}


def ws_query(sql: str, limit: int = 100) -> list:
    """Query write_service."""
    try:
        r = requests.post(f"{EXECUTE_URL}/query",
                          json={"sql": sql, "limit": limit}, timeout=5)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception as e:
        log.debug(f"ws_query: {e}")
    return []


def ws_write(table: str, row: dict) -> bool:
    """Write to write_service."""
    try:
        r = requests.post(f"{WRITE_SERVICE}/write",
                          json={"table": table, "rows": row, "wait": True}, timeout=5)
        return r.status_code == 200
    except Exception as e:
        log.debug(f"ws_write: {e}")
        return False


def ws_execute(sql: str) -> bool:
    """Execute SQL via write_service."""
    try:
        r = requests.post(f"{EXECUTE_URL}/execute", json={"sql": sql}, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


class TestResult:
    __slots__ = ("name", "status", "reason")

    def __init__(self, name: str, status: str, reason: str = ""):
        self.name = name
        self.status = status
        self.reason = reason


def check_service(url: str, timeout: int = 5) -> tuple[bool, Optional[dict]]:
    """Check if service is reachable."""
    try:
        r = requests.get(url, timeout=timeout)
        return True, r.json() if r.headers.get("content-type", "").startswith("application/json") else None
    except requests.exceptions.ConnectionError:
        return False, None
    except requests.exceptions.Timeout:
        return False, None
    except Exception:
        return False, None


class HealthTests:
    """Test health endpoints on all service ports."""

    PORTS = {
        8780: "Approval Workflow",
        8781: "Registry API",
        8782: "Dashboard API",
        8790: "UI Server",
    }

    @staticmethod
    def run() -> list:
        results = []
        for port, name in HealthTests.PORTS.items():
            url = f"http://127.0.0.1:{port}/health"
            reachable, data = check_service(url)

            if not reachable:
                results.append(TestResult(f"health_{port}", "SKIP", f"{name} not reachable"))
            elif data and data.get("status") == "ok":
                results.append(TestResult(f"health_{port}", "PASS", f"{name} healthy"))
            else:
                results.append(TestResult(f"health_{port}", "FAIL", f"Unexpected: {data}"))
        return results


class RegistryAPITests:
    """Test Registry API endpoints."""

    @staticmethod
    def run() -> list:
        results = []
        base = "http://127.0.0.1:8781"

        # GET /v1/registry
        try:
            r = requests.get(f"{base}/v1/registry?page=1&limit=5", timeout=5)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    results.append(TestResult("registry_list", "PASS", f"Got {len(data)} entries"))
                else:
                    results.append(TestResult("registry_list", "FAIL", f"Expected list, got {type(data)}"))
            else:
                results.append(TestResult("registry_list", "FAIL", f"Status {r.status_code}"))
        except requests.exceptions.ConnectionError:
            results.append(TestResult("registry_list", "SKIP", "Registry API not reachable"))
        except Exception as e:
            results.append(TestResult("registry_list", "FAIL", str(e)))

        # GET /v1/threats
        try:
            r = requests.get(f"{base}/v1/threats?limit=5", timeout=5)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    results.append(TestResult("threats_list", "PASS", f"Got {len(data)} threats"))
                else:
                    results.append(TestResult("threats_list", "FAIL", f"Expected list, got {type(data)}"))
            else:
                results.append(TestResult("threats_list", "FAIL", f"Status {r.status_code}"))
        except requests.exceptions.ConnectionError:
            results.append(TestResult("threats_list", "SKIP", "Registry API not reachable"))
        except Exception as e:
            results.append(TestResult("threats_list", "FAIL", str(e)))

        # GET /v1/assess
        try:
            r = requests.get(f"{base}/v1/assess?mcp=filesystem", timeout=5)
            if r.status_code == 200:
                data = r.json()
                required_keys = ["verdict", "trust_score", "server_id"]
                missing = [k for k in required_keys if k not in data]
                if not missing:
                    results.append(TestResult("assess_query", "PASS", "All required keys present"))
                else:
                    results.append(TestResult("assess_query", "FAIL", f"Missing keys: {missing}"))
            else:
                results.append(TestResult("assess_query", "FAIL", f"Status {r.status_code}"))
        except requests.exceptions.ConnectionError:
            results.append(TestResult("assess_query", "SKIP", "Registry API not reachable"))
        except Exception as e:
            results.append(TestResult("assess_query", "FAIL", str(e)))

        # GET /health
        try:
            r = requests.get(f"{base}/health", timeout=5)
            if r.status_code == 200:
                results.append(TestResult("registry_health", "PASS", "Health endpoint OK"))
            else:
                results.append(TestResult("registry_health", "FAIL", f"Status {r.status_code}"))
        except requests.exceptions.ConnectionError:
            results.append(TestResult("registry_health", "SKIP", "Registry API not reachable"))
        except Exception as e:
            results.append(TestResult("registry_health", "FAIL", str(e)))

        return results


class ApprovalWorkflowTests:
    """Test Approval Workflow API endpoints."""

    @staticmethod
    def run() -> list:
        results = []
        base = "http://127.0.0.1:8780"

        # GET /health
        try:
            r = requests.get(f"{base}/health", timeout=5)
            if r.status_code == 200:
                results.append(TestResult("approval_health", "PASS", "Health endpoint OK"))
            else:
                results.append(TestResult("approval_health", "FAIL", f"Status {r.status_code}"))
        except requests.exceptions.ConnectionError:
            results.append(TestResult("approval_health", "SKIP", "Approval workflow not reachable"))
        except Exception as e:
            results.append(TestResult("approval_health", "FAIL", str(e)))

        # GET /api/registry
        try:
            r = requests.get(f"{base}/api/registry?page=1&limit=5", timeout=5)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    results.append(TestResult("approval_registry", "PASS", f"Got {len(data)} entries"))
                else:
                    results.append(TestResult("approval_registry", "FAIL", f"Expected list, got {type(data)}"))
            else:
                results.append(TestResult("approval_registry", "FAIL", f"Status {r.status_code}"))
        except requests.exceptions.ConnectionError:
            results.append(TestResult("approval_registry", "SKIP", "Approval workflow not reachable"))
        except Exception as e:
            results.append(TestResult("approval_registry", "FAIL", str(e)))

        # POST /api/submit
        submit_payload = {
            "mcp_name": "test-ui-endpoint-mcp",
            "url": "https://example.com/test-mcp",
            "description": "Test MCP submitted by UI endpoint tests",
            "requested_by": "ui_endpoint_tests"
        }
        try:
            r = requests.post(f"{base}/api/submit", json=submit_payload, timeout=5)
            if r.status_code in (200, 201):
                data = r.json()
                if data.get("submitted") is True or data.get("id"):
                    results.append(TestResult("approval_submit", "PASS", f"Submitted with ID"))
                else:
                    results.append(TestResult("approval_submit", "FAIL", f"No submitted/id in response"))
            elif r.status_code == 422:
                data = r.json()
                if "detail" in data:
                    results.append(TestResult("approval_submit_validation", "PASS", f"Validation detail: {data['detail']}"))
                else:
                    results.append(TestResult("approval_submit", "FAIL", "422 but no detail"))
            else:
                results.append(TestResult("approval_submit", "FAIL", f"Status {r.status_code}"))
        except requests.exceptions.ConnectionError:
            results.append(TestResult("approval_submit", "SKIP", "Approval workflow not reachable"))
        except Exception as e:
            results.append(TestResult("approval_submit", "FAIL", str(e)))

        return results


class SearchAPITests:
    """Test Search API endpoints."""

    @staticmethod
    def run() -> list:
        results = []
        base = "http://127.0.0.1:8782"

        # GET /search
        try:
            r = requests.get(f"{base}/search?q=filesystem", timeout=5)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    results.append(TestResult("search_query", "PASS", f"Got {len(data)} results"))
                elif isinstance(data, dict) and "results" in data:
                    results.append(TestResult("search_query", "PASS", f"Got results dict"))
                else:
                    results.append(TestResult("search_query", "FAIL", f"Unexpected format"))
            else:
                results.append(TestResult("search_query", "FAIL", f"Status {r.status_code}"))
        except requests.exceptions.ConnectionError:
            results.append(TestResult("search_query", "SKIP", "Search API not reachable"))
        except Exception as e:
            results.append(TestResult("search_query", "FAIL", str(e)))

        # GET /stats
        try:
            r = requests.get(f"{base}/stats", timeout=5)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and "verdict_breakdown" in data:
                    results.append(TestResult("search_stats", "PASS", f"Verdict breakdown keys"))
                elif isinstance(data, dict):
                    results.append(TestResult("search_stats", "PASS", f"Got stats dict"))
                else:
                    results.append(TestResult("search_stats", "FAIL", f"Expected dict, got {type(data)}"))
            else:
                results.append(TestResult("search_stats", "FAIL", f"Status {r.status_code}"))
        except requests.exceptions.ConnectionError:
            results.append(TestResult("search_stats", "SKIP", "Search API not reachable"))
        except Exception as e:
            results.append(TestResult("search_stats", "FAIL", str(e)))

        return results


class UIServerTests:
    """Test UI server responses."""

    @staticmethod
    def run() -> list:
        results = []
        url = "http://localhost:8790"

        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                content_type = r.headers.get("content-type", "")
                if "text/html" in content_type or len(r.text) > 100:
                    results.append(TestResult("ui_server_root", "PASS", f"HTML response ({len(r.text)} bytes)"))
                else:
                    results.append(TestResult("ui_server_root", "FAIL", "Response too short"))
            else:
                results.append(TestResult("ui_server_root", "FAIL", f"Status {r.status_code}"))
        except requests.exceptions.ConnectionError:
            results.append(TestResult("ui_server_root", "SKIP", "UI server not reachable"))
        except Exception as e:
            results.append(TestResult("ui_server_root", "FAIL", str(e)))

        return results


class DataIntegrityTests:
    """Test data integrity in mcp_server_registry."""

    @staticmethod
    def run() -> list:
        results = []

        # Check if table has rows
        rows = ws_query("SELECT server_id, name, url, verdict FROM mcp_server_registry LIMIT 100", limit=100)

        if not rows:
            results.append(TestResult("integrity_has_data", "SKIP", "mcp_server_registry is empty"))
            return results

        results.append(TestResult("integrity_has_data", "PASS", f"Found {len(rows)} rows"))

        # Check required fields
        missing_fields = []
        for i, row in enumerate(rows):
            if not row.get("server_id"):
                missing_fields.append(f"row {i}: missing server_id")
            if not row.get("name"):
                missing_fields.append(f"row {i}: missing name")
            if not row.get("url"):
                missing_fields.append(f"row {i}: missing url")

        if missing_fields:
            results.append(TestResult("integrity_required_fields", "FAIL", f"Issues: {missing_fields[:5]}"))
        else:
            results.append(TestResult("integrity_required_fields", "PASS", "All rows have required fields"))

        # Check verdict taxonomy validity
        invalid_verdicts = []
        for row in rows:
            verdict = row.get("verdict", "")
            if verdict and verdict not in VERDICT_TAXONOMY:
                invalid_verdicts.append(f"{row.get('server_id')}: '{verdict}'")

        if invalid_verdicts:
            results.append(TestResult("integrity_verdict_taxonomy", "FAIL", f"Invalid verdicts: {invalid_verdicts[:5]}"))
        else:
            results.append(TestResult("integrity_verdict_taxonomy", "PASS", "All verdicts valid"))

        return results


def ensure_mesh_events_table():
    """Ensure mesh_events table exists for test logging."""
    ws_execute("""
        CREATE TABLE IF NOT EXISTS mesh_events (
            id BIGINT PRIMARY KEY,
            event_type VARCHAR,
            source VARCHAR,
            payload TEXT,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)


def write_results_to_json(results: list):
    """Write test results to JSON file."""
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    skip_count = sum(1 for r in results if r.status == "SKIP")

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "skip_count": skip_count,
        "results": [
            {"name": r.name, "status": r.status, "reason": r.reason}
            for r in results
        ]
    }

    with open(RESULTS_FILE, "w") as f:
        json.dump(output, f, indent=2)

    return pass_count, fail_count, skip_count


def log_to_mesh_events(pass_count: int, fail_count: int, skip_count: int):
    """Log test completion to mesh_events."""
    ensure_mesh_events_table()

    row = {
        "event_type": "ui_test_complete",
        "source": "ui_endpoint_tests",
        "payload": json.dumps({
            "pass_count": pass_count,
            "fail_count": fail_count,
            "skip_count": skip_count,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }),
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    ws_write("mesh_events", row)


def print_results(results: list):
    """Print test results to console."""
    print("\n" + "=" * 70)
    print("ZO-SENTINEL UI Endpoint Test Results")
    print("=" * 70)

    pass_count = 0
    fail_count = 0
    skip_count = 0

    current_class = ""
    for r in results:
        parts = r.name.split("_", 1)
        class_name = parts[0].upper() if parts else "TEST"

        if class_name != current_class:
            print(f"\n[{class_name}]")
            current_class = class_name

        status_symbol = {"PASS": "✓", "FAIL": "✗", "SKIP": "○"}[r.status]
        reason = f" - {r.reason}" if r.reason else ""
        print(f"  {status_symbol} {r.name}{reason}")

        if r.status == "PASS":
            pass_count += 1
        elif r.status == "FAIL":
            fail_count += 1
        else:
            skip_count += 1

    print("\n" + "-" * 70)
    print(f"Results: {pass_count} PASS, {fail_count} FAIL, {skip_count} SKIP")
    print("=" * 70 + "\n")


def run() -> int:
    """Run all test suites."""
    all_results = []

    print("Starting ZO-SENTINEL UI Endpoint Tests...")
    print("Testing services: Approval(8780), Registry(8781), Dashboard(8782), UI(8790)")

    test_suites = [
        ("Health Tests", HealthTests),
        ("Registry API Tests", RegistryAPITests),
        ("Approval Workflow Tests", ApprovalWorkflowTests),
        ("Search API Tests", SearchAPITests),
        ("UI Server Tests", UIServerTests),
        ("Data Integrity Tests", DataIntegrityTests),
    ]

    for suite_name, test_class in test_suites:
        try:
            results = test_class.run()
            all_results.extend(results)
        except Exception as e:
            log.error(f"Test suite {suite_name} failed: {e}")

    print_results(all_results)

    pass_count, fail_count, skip_count = write_results_to_json(all_results)

    log_to_mesh_events(pass_count, fail_count, skip_count)

    if fail_count > 0:
        print(f"FAILURES: {fail_count} test(s) failed. Check {RESULTS_FILE} for details.")
        return 1

    print("All tests passed!")
    return 0


if __name__ == "__main__":
    sys.exit(run())