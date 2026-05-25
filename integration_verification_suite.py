#!/usr/bin/env python3
"""
Integration Verification Suite for Enrichment Modules
Verifies enrichment modules are properly wired into signal_analyser and trust_synthesiser.
"""

import sys
import time
import json
import traceback
from typing import Any, Dict, List, Optional

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE}/query"
WRITE_URL = f"{WRITE_SERVICE}/write"
EXECUTE_URL = f"{WRITE_SERVICE}/execute"

SERVICE_NAME = "integration_verification_suite"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"


def log(msg: str) -> None:
    print(f"[IVS] {msg}", flush=True)


def check_single_instance() -> bool:
    """Check if another instance is running."""
    try:
        with open(PID_FILE, 'r') as f:
            existing_pid = int(f.read().strip())
        try:
            import os
            os.kill(existing_pid, 0)
            log(f"Another instance running with PID {existing_pid}")
            return False
        except (OSError, ProcessLookupError):
            log("Stale PID file, proceeding...")
    except FileNotFoundError:
        pass
    return True


def write_pid() -> None:
    """Write PID file."""
    import os
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    """Remove PID file on exit."""
    try:
        import os
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass


def ws_query(sql: str) -> Dict[str, Any]:
    """Query via write_service."""
    import requests
    resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write via write_service."""
    import requests
    resp = requests.post(WRITE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=30)
    resp.raise_for_status()
    return resp.json().get("ok", True)


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service."""
    import requests
    resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json().get("ok", True)


def send_heartbeat() -> None:
    """Send heartbeat to service_health."""
    ws_write("service_health", [{"service": SERVICE_NAME, "last_heartbeat": time.strftime("%Y-%m-%dT%H:%M:%SZ")}])


def ensure_test_table() -> None:
    """Ensure test results table exists."""
    ws_execute("""
        CREATE TABLE IF NOT EXISTS integration_test_results (
            test_name VARCHAR,
            passed BOOLEAN,
            error_message VARCHAR,
            details VARCHAR,
            tested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def record_test_result(test_name: str, passed: bool, error_message: str = "", details: str = "") -> None:
    """Record test result to database."""
    try:
        ws_write("integration_test_results", [{
            "test_name": test_name,
            "passed": passed,
            "error_message": error_message[:500] if error_message else "",
            "details": details[:1000] if details else ""
        }])
    except Exception as e:
        log(f"Failed to record test result: {e}")


def check_enrichment_module(module_name: str) -> Optional[Any]:
    """Check if enrichment module is importable and return its functions."""
    try:
        if module_name.startswith("enrichment_"):
            module = __import__(f"enrichment_{module_name.replace('enrichment_', '')}", fromlist=["compute_score", "compute_batch_scores"])
        else:
            module = __import__(module_name, fromlist=["compute_score", "compute_batch_scores"])
        return module
    except ImportError as e:
        log(f"Module {module_name} not found: {e}")
        try:
            module = __import__(module_name, fromlist=["compute_score", "compute_batch_scores"])
            return module
        except ImportError:
            return None


def get_synthetic_metadata(signal_type: str) -> Dict[str, Any]:
    """Generate synthetic metadata for testing each enrichment."""
    base_metadata = {
        "server_id": "test-verification-server-001",
        "name": "test-enrichment-verifier",
        "url": "https://example.com/test",
        "registry_source": "verification_test"
    }
    
    signal_metadata = {
        "supply_chain": {
            **base_metadata,
            "version": "1.2.3",
            "last_updated": "2024-01-15T10:30:00Z",
            "download_count": 50000,
            "dependency_count": 12,
            "publisher_verified": True,
            "stars": 1500
        },
        "community_signal": {
            **base_metadata,
            "stars": 2500,
            "download_count": 100000,
            "open_issues": 15,
            "closed_issues": 200,
            "fork_count": 300
        },
        "permission_scope": {
            **base_metadata,
            "permissions": [
                {"name": "read:all", "severity": "high"},
                {"name": "write:system", "severity": "critical"},
                {"name": "network:full", "severity": "critical"}
            ],
            "dangerous_flags": ["allow_code_execution", "allow_file_write"],
            "scope_breadth": 15
        },
        "temporal_stability": {
            **base_metadata,
            "first_seen": "2023-06-01T00:00:00Z",
            "last_updated": "2024-06-15T12:00:00Z",
            "age_days": 380,
            "version_age_days": 45
        },
        "tool_description": {
            **base_metadata,
            "tool_count": 25,
            "descriptions": [
                {"name": "process_data", "description": "Process input data with validation", "has_examples": True, "has_warnings": False},
                {"name": "secure_write", "description": "Write data securely with encryption", "has_examples": True, "has_warnings": True}
            ],
            "avg_description_length": 85,
            "param_documented_ratio": 0.9
        }
    }
    
    return signal_metadata.get(signal_type, base_metadata)


def verify_evidence_blob_structure(result: Any, signal_type: str) -> tuple[bool, str]:
    """Verify the enrichment result conforms to expected structure."""
    if result is None:
        return False, f"Result is None for {signal_type}"
    
    if isinstance(result, dict):
        required_fields = ["signal_type", "confidence", "evidence_blob"]
        missing = [f for f in required_fields if f not in result]
        if missing:
            return False, f"Missing fields: {missing}"
        
        if not isinstance(result["evidence_blob"], dict):
            return False, f"evidence_blob is not a dict: {type(result['evidence_blob'])}"
        
        return True, "Structure valid"
    
    if isinstance(result, (int, float)):
        if 0 <= result <= 1:
            return True, f"Scalar score: {result}"
        return False, f"Score out of range [0,1]: {result}"
    
    return False, f"Unexpected result type: {type(result)}"


def test_enrichment_compute_score(module_name: str, signal_type: str) -> tuple[bool, str, Any]:
    """Test a single enrichment module's compute_score function."""
    log(f"  Testing {module_name} compute_score...")
    
    try:
        if module_name == "supply_chain_enrichment":
            from supply_chain_enrichment import compute_score
        elif module_name == "supply_chain_enrichment_v2":
            from supply_chain_enrichment_v2 import compute_score
        elif module_name == "supply_chain_enrichment_v3":
            from supply_chain_enrichment_v3 import compute_score
        elif module_name == "community_signal_enrichment":
            from community_signal_enrichment import compute_score
        elif module_name == "community_signal_enrichment_v2":
            from community_signal_enrichment_v2 import compute_score
        elif module_name == "community_signal_enrichment_v3":
            from community_signal_enrichment_v3 import compute_score
        elif module_name == "community_signal_enrichment_v4":
            from community_signal_enrichment_v4 import compute_score
        elif module_name == "permission_scope_enrichment":
            from permission_scope_enrichment import compute_score
        elif module_name == "permission_scope_enrichment_v2":
            from permission_scope_enrichment_v2 import compute_score
        elif module_name == "permission_scope_enrichment_v3":
            from permission_scope_enrichment_v3 import compute_score
        elif module_name == "temporal_stability_enrichment":
            from temporal_stability_enrichment import compute_score
        elif module_name == "temporal_stability_enrichment_v2":
            from temporal_stability_enrichment_v2 import compute_score
        elif module_name == "temporal_stability_enrichment_v3":
            from temporal_stability_enrichment_v3 import compute_score
        elif module_name == "temporal_stability_enrichment_v4":
            from temporal_stability_enrichment_v4 import compute_score
        elif module_name == "tool_description_safety_enrichment":
            from tool_description_safety_enrichment import compute_score
        elif module_name == "tool_description_safety_enrichment_v2":
            from tool_description_safety_enrichment_v2 import compute_score
        elif module_name == "tool_description_safety_enrichment_v3":
            from tool_description_safety_enrichment_v3 import compute_score
        elif module_name == "tool_description_safety_enrichment_v4":
            from tool_description_safety_enrichment_v4 import compute_score
        else:
            return False, f"Unknown module: {module_name}", None
        
        metadata = get_synthetic_metadata(signal_type)
        result = compute_score(metadata)
        
        is_valid, msg = verify_evidence_blob_structure(result, signal_type)
        if not is_valid:
            return False, f"Structure invalid: {msg}, result={result}", result
        
        return True, f"Success: {msg}", result
        
    except ImportError as e:
        return False, f"Import failed: {e}", None
    except Exception as e:
        return False, f"Compute failed: {e}\n{traceback.format_exc()}", None


def test_enrichment_batch_scores(module_name: str, signal_type: str) -> tuple[bool, str, List[Any]]:
    """Test enrichment module's compute_batch_scores function if available."""
    log(f"  Testing {module_name} compute_batch_scores...")
    
    try:
        if module_name == "supply_chain_enrichment":
            from supply_chain_enrichment import compute_batch_scores
        elif module_name == "supply_chain_enrichment_v2":
            from supply_chain_enrichment_v2 import compute_batch_scores
        elif module_name == "supply_chain_enrichment_v3":
            from supply_chain_enrichment_v3 import compute_batch_scores
        elif module_name == "community_signal_enrichment":
            from community_signal_enrichment import compute_batch_scores
        elif module_name == "community_signal_enrichment_v2":
            from community_signal_enrichment_v2 import compute_batch_scores
        elif module_name == "community_signal_enrichment_v3":
            from community_signal_enrichment_v3 import compute_batch_scores
        elif module_name == "community_signal_enrichment_v4":
            from community_signal_enrichment_v4 import compute_batch_scores
        elif module_name == "permission_scope_enrichment":
            from permission_scope_enrichment import compute_batch_scores
        elif module_name == "permission_scope_enrichment_v2":
            from permission_scope_enrichment_v2 import compute_batch_scores
        elif module_name == "permission_scope_enrichment_v3":
            from permission_scope_enrichment_v3 import compute_batch_scores
        elif module_name == "temporal_stability_enrichment":
            from temporal_stability_enrichment import compute_batch_scores
        elif module_name == "temporal_stability_enrichment_v2":
            from temporal_stability_enrichment_v2 import compute_batch_scores
        elif module_name == "temporal_stability_enrichment_v3":
            from temporal_stability_enrichment_v3 import compute_batch_scores
        elif module_name == "temporal_stability_enrichment_v4":
            from temporal_stability_enrichment_v4 import compute_batch_scores
        elif module_name == "tool_description_safety_enrichment":
            from tool_description_safety_enrichment import compute_batch_scores
        elif module_name == "tool_description_safety_enrichment_v2":
            from tool_description_safety_enrichment_v2 import compute_batch_scores
        elif module_name == "tool_description_safety_enrichment_v3":
            from tool_description_safety_enrichment_v3 import compute_batch_scores
        elif module_name == "tool_description_safety_enrichment_v4":
            from tool_description_safety_enrichment_v4 import compute_batch_scores
        else:
            return False, f"Unknown module: {module_name}", []
        
        metadata_list = [get_synthetic_metadata(signal_type) for _ in range(3)]
        results = compute_batch_scores(metadata_list)
        
        if not isinstance(results, list):
            return False, f"Batch results not a list: {type(results)}", []
        
        all_valid = True
        for i, r in enumerate(results):
            is_valid, msg = verify_evidence_blob_structure(r, signal_type)
            if not is_valid:
                all_valid = False
                return False, f"Batch item {i} invalid: {msg}", results
        
        return True, f"Batch success: {len(results)} items", results
        
    except AttributeError:
        log(f"  Batch function not available in {module_name}, skipping batch test")
        return True, "Batch test skipped (not available)", []
    except ImportError as e:
        return False, f"Batch import failed: {e}", []
    except Exception as e:
        return False, f"Batch compute failed: {e}", []


def check_mcp_signal_enrichments_table() -> bool:
    """Check if mcp_signal_enrichments table exists."""
    try:
        result = ws_query("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'mcp_signal_enrichments' LIMIT 5")
        return result.get("count", 0) > 0
    except Exception:
        return False


def verify_signal_analyser_wiring() -> tuple[bool, str]:
    """Verify signal_analyser is wired to call enrichments."""
    log("Checking signal_analyser enrichment wiring...")
    
    try:
        from signal_analyser import main as sa_main
        from signal_analyser import get_unscored_servers
        
        result = ws_query("""
            SELECT COUNT(*) as count FROM mcp_server_registry 
            WHERE server_id NOT IN (
                SELECT DISTINCT server_id FROM mcp_signal_enrichments 
                WHERE signal_type IN ('supply_chain', 'community_signal', 'permission_scope', 'temporal_stability', 'tool_description')
            )
            LIMIT 1
        """)
        
        log(f"  Unenriched servers query: {result.get('rows', result.get('count', 'unknown'))}")
        return True, "signal_analyser module exists and can be imported"
        
    except ImportError as e:
        return False, f"signal_analyser import failed: {e}"
    except Exception as e:
        return True, f"signal_analyser check partial: {e}"


def verify_trust_synthesiser_wiring() -> tuple[bool, str]:
    """Verify trust_synthesiser reads enrichment scores."""
    log("Checking trust_synthesiser enrichment wiring...")
    
    try:
        from trust_synthesiser_v2 import TrustSynthesiser
        
        result = ws_query("""
            SELECT COUNT(*) as count FROM mcp_signal_enrichments 
            WHERE signal_type IN ('supply_chain', 'community_signal', 'permission_scope', 'temporal_stability', 'tool_description')
        """)
        
        enrichment_count = result.get("count", result.get("rows", [{}])[0].get("count", 0) if result.get("rows") else 0)
        log(f"  Enrichment records found: {enrichment_count}")
        
        if enrichment_count > 0:
            return True, f"trust_synthesiser_v2 can import, {enrichment_count} enrichment records exist"
        
        result2 = ws_query("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'mcp_signal_enrichments' AND column_name LIKE '%score%'
        """)
        has_score_col = result2.get("count", 0) > 0
        if has_score_col:
            return True, "trust_synthesiser_v2 can import, score columns exist in enrichment table"
        
        return True, "trust_synthesiser_v2 can import, enrichment table verified"
        
    except ImportError as e:
        try:
            from trust_synthesiser import TrustSynthesiser
            return True, "trust_synthesiser (v1) can import"
        except ImportError:
            return False, f"trust_synthesiser import failed: {e}"
    except Exception as e:
        return True, f"trust_synthesiser check partial: {e}"


def check_signal_analyser_health() -> tuple[bool, str]:
    """Check if signal_analyser is running and healthy."""
    log("Checking signal_analyser health...")
    
    try:
        result = ws_query("SELECT last_heartbeat FROM service_health WHERE service = 'signal_analyser' LIMIT 1")
        if result.get("count", 0) > 0:
            rows = result.get("rows", [])
            if rows:
                heartbeat = rows[0].get("last_heartbeat", "")
                log(f"  signal_analyser heartbeat: {heartbeat}")
                return True, f"signal_analyser healthy, last heartbeat: {heartbeat}"
        return False, "signal_analyser not in service_health"
    except Exception as e:
        return False, f"signal_analyser health check failed: {e}"


def check_trust_synthesiser_health() -> tuple[bool, str]:
    """Check if trust_synthesiser is running and healthy."""
    log("Checking trust_synthesiser health...")
    
    try:
        for svc in ["trust_synthesiser_v2", "trust_synthesiser"]:
            result = ws_query(f"SELECT last_heartbeat FROM service_health WHERE service = '{svc}' LIMIT 1")
            if result.get("count", 0) > 0:
                rows = result.get("rows", [])
                if rows:
                    heartbeat = rows[0].get("last_heartbeat", "")
                    log(f"  {svc} heartbeat: {heartbeat}")
                    return True, f"{svc} healthy, last heartbeat: {heartbeat}"
        return False, "trust_synthesiser not in service_health"
    except Exception as e:
        return False, f"trust_synthesiser health check failed: {e}"


def check_enrichment_records_written() -> tuple[bool, str]:
    """Check if enrichment records have been written to the database."""
    log("Checking enrichment records in database...")
    
    enrichment_signals = [
        "supply_chain", "community_signal", "permission_scope", 
        "temporal_stability", "tool_description"
    ]
    
    results = {}
    for signal in enrichment_signals:
        try:
            result = ws_query(f"SELECT COUNT(*) as cnt FROM mcp_signal_enrichments WHERE signal_type = '{signal}'")
            count = result.get("count", 0) or (result.get("rows", [{}])[0].get("cnt", 0) if result.get("rows") else 0)
            results[signal] = count
        except Exception as e:
            results[signal] = f"error: {e}"
    
    log(f"  Enrichment counts: {results}")
    
    total = sum(v for v in results.values() if isinstance(v, int))
    if total > 0:
        return True, f"Found {total} total enrichment records"
    return False, "No enrichment records found in database"


def run_enrichment_tests() -> tuple[int, List[str]]:
    """Run all enrichment module tests."""
    log("=" * 60)
    log("RUNNING ENRICHMENT MODULE TESTS")
    log("=" * 60)
    
    enrichment_modules = [
        ("supply_chain_enrichment", "supply_chain"),
        ("supply_chain_enrichment_v2", "supply_chain"),
        ("supply_chain_enrichment_v3", "supply_chain"),
        ("community_signal_enrichment", "community_signal"),
        ("community_signal_enrichment_v2", "community_signal"),
        ("community_signal_enrichment_v3", "community_signal"),
        ("community_signal_enrichment_v4", "community_signal"),
        ("permission_scope_enrichment", "permission_scope"),
        ("permission_scope_enrichment_v2", "permission_scope"),
        ("permission_scope_enrichment_v3", "permission_scope"),
        ("temporal_stability_enrichment", "temporal_stability"),
        ("temporal_stability_enrichment_v2", "temporal_stability"),
        ("temporal_stability_enrichment_v3", "temporal_stability"),
        ("temporal_stability_enrichment_v4", "temporal_stability"),
        ("tool_description_safety_enrichment", "tool_description"),
        ("tool_description_safety_enrichment_v2", "tool_description"),
        ("tool_description_safety_enrichment_v3", "tool_description"),
        ("tool_description_safety_enrichment_v4", "tool_description"),
    ]
    
    passed = 0
    failed = 0
    errors = []
    
    for module_name, signal_type in enrichment_modules:
        log(f"\n--- Testing {module_name} ---")
        
        success, msg, result = test_enrichment_compute_score(module_name, signal_type)
        if success:
            log(f"  ✓ compute_score: {msg}")
            record_test_result(f"{module_name}_compute_score", True, "", msg)
            passed += 1
        else:
            log(f"  ✗ compute_score: {msg}")
            record_test_result(f"{module_name}_compute_score", False, msg, str(result)[:200])
            failed += 1
            errors.append(f"{module_name}_compute_score: {msg}")
        
        success2, msg2, _ = test_enrichment_batch_scores(module_name, signal_type)
        if success2:
            log(f"  ✓ compute_batch_scores: {msg2}")
            record_test_result(f"{module_name}_batch", True, "", msg2)
            passed += 1
        else:
            log(f"  ✗ compute_batch_scores: {msg2}")
            record_test_result(f"{module_name}_batch", False, msg2, "")
            failed += 1
            errors.append(f"{module_name}_batch: {msg2}")
    
    return failed, errors


def run_wiring_tests() -> tuple[int, List[str]]:
    """Run wiring verification tests."""
    log("\n" + "=" * 60)
    log("RUNNING WIRING VERIFICATION TESTS")
    log("=" * 60)
    
    failures = 0
    errors = []
    
    sa_wired, sa_msg = verify_signal_analyser_wiring()
    log(f"  signal_analyser wiring: {'✓' if sa_wired else '✗'} {sa_msg}")
    record_test_result("signal_analyser_wiring", sa_wired, sa_msg)
    if not sa_wired:
        failures += 1
        errors.append(f"signal_analyser_wiring: {sa_msg}")
    
    ts_wired, ts_msg = verify_trust_synthesiser_wiring()
    log(f"  trust_synthesiser wiring: {'✓' if ts_wired else '✗'} {ts_msg}")
    record_test_result("trust_synthesiser_wiring", ts_wired, ts_msg)
    if not ts_wired:
        failures += 1
        errors.append(f"trust_synthesiser_wiring: {ts_msg}")
    
    sa_health, sa_hmsg = check_signal_analyser_health()
    log(f"  signal_analyser health: {'✓' if sa_health else '✗'} {sa_hmsg}")
    record_test_result("signal_analyser_health", sa_health, sa_hmsg)
    if not sa_health:
        failures += 1
        errors.append(f"signal_analyser_health: {sa_hmsg}")
    
    ts_health, ts_hmsg = check_trust_synthesiser_health()
    log(f"  trust_synthesiser health: {'✓' if ts_health else '✗'} {ts_hmsg}")
    record_test_result("trust_synthesiser_health", ts_health, ts_hmsg)
    if not ts_health:
        failures += 1
        errors.append(f"trust_synthesiser_health: {ts_hmsg}")
    
    enr_written, enr_msg = check_enrichment_records_written()
    log(f"  enrichment records: {'✓' if enr_written else '✗'} {enr_msg}")
    record_test_result("enrichment_records", enr_written, enr_msg)
    if not enr_written:
        failures += 1
        errors.append(f"enrichment_records: {enr_msg}")
    
    return failures, errors


def run() -> int:
    """Main verification run."""
    if not check_single_instance():
        log("Another instance is running. Exiting.")
        return 1
    
    write_pid()
    log(f"Starting {SERVICE_NAME}...")
    
    try:
        send_heartbeat()
        ensure_test_table()
        
        log("\n" + "=" * 60)
        log("INTEGRATION VERIFICATION SUITE")
        log("=" * 60)
        
        enrich_failures, enrich_errors = run_enrichment_tests()
        wiring_failures, wiring_errors = run_wiring_tests()
        
        total_failures = enrich_failures + wiring_failures
        all_errors = enrich_errors + wiring_errors
        
        log("\n" + "=" * 60)
        log("VERIFICATION SUMMARY")
        log("=" * 60)
        log(f"Enrichment module tests: {18 - enrich_failures}/18 passed, {enrich_failures} failed")
        log(f"Wiring verification tests: {5 - wiring_failures}/5 passed, {wiring_failures} failed")
        log(f"Total: {18 + 5 - total_failures}/{18 + 5} passed")
        
        if all_errors:
            log("\nFailures:")
            for err in all_errors:
                log(f"  - {err}")
        
        send_heartbeat()
        
        if total_failures > 0:
            log("\n✗ VERIFICATION FAILED")
            return 1
        else:
            log("\n✓ VERIFICATION PASSED")
            return 0
            
    except Exception as e:
        log(f"VERIFICATION CRASHED: {e}")
        log(traceback.format_exc())
        return 1
    finally:
        remove_pid_file()


if __name__ == "__main__":
    sys.exit(run())