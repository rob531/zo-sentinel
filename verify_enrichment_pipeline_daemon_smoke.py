import os
import sys
import time
import signal
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

import requests

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
SERVICE_NAME = "verify_enrichment_pipeline_daemon_smoke"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = os.path.join(LOG_DIR, f"{SERVICE_NAME}.log")

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

PID_FILE = f"/tmp/{SERVICE_NAME}.pid"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, timeout: float = 30.0) -> Optional[List[Dict[str, Any]]]:
    try:
        resp = requests.post(
            QUERY_URL,
            json={"sql": sql},
            timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return None


def ws_write(table: str, rows: List[Dict[str, Any]], timeout: float = 30.0) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=timeout
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed for table {table}: {e}")
        return False


def ws_execute(sql: str, timeout: float = 30.0) -> bool:
    try:
        resp = requests.post(
            EXECUTE_URL,
            json={"sql": sql},
            timeout=timeout
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            pid = int(f.read().strip())
        try:
            os.kill(pid, 0)
            log.warning(f"Another instance already running with PID {pid}")
            return False
        except OSError:
            log.info("Stale PID file found, removing it")
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def check_enrichment_pipeline_daemon() -> Dict[str, Any]:
    result = {
        "passed": True,
        "checks": [],
        "errors": []
    }
    
    log.info("Starting enrichment pipeline daemon smoke verification")
    
    check_enrichment_modules_exist(result)
    check_enrichment_wiring(result)
    check_signal_analyser_integration(result)
    check_service_health_entries(result)
    check_mcp_signal_enrichments_table(result)
    check_write_service_connectivity(result)
    
    result["passed"] = len(result["errors"]) == 0
    return result


def check_enrichment_modules_exist(result: Dict[str, Any]):
    check_name = "enrichment_modules_exist"
    log.info(f"Running check: {check_name}")
    
    enrichment_modules = [
        "supply_chain_enrichment_v3",
        "community_signal_enrichment_v4",
        "temporal_stability_enrichment_v3",
        "permission_scope_enrichment_v2",
        "tool_description_safety_enrichment_v2",
        "domain_trust_enrichment_v2",
        "context_efficiency_enrichment",
        "evidence_density_enrichment",
        "registry_breadth_enrichment",
        "vendor_concentration_enrichment",
        "injection_resilience_enrichment"
    ]
    
    sentinel_path = "/home/workspace/zo_sentinel"
    missing = []
    for module in enrichment_modules:
        module_path = os.path.join(sentinel_path, f"{module}.py")
        if not os.path.exists(module_path):
            missing.append(module)
    
    if missing:
        result["errors"].append(f"Missing enrichment modules: {missing}")
        log.error(f"Missing modules: {missing}")
    else:
        log.info(f"All {len(enrichment_modules)} enrichment modules found")
        result["checks"].append(check_name)


def check_enrichment_wiring(result: Dict[str, Any]):
    check_name = "enrichment_wiring"
    log.info(f"Running check: {check_name}")
    
    wiring_modules = [
        "supply_chain_enrichment_wiring",
        "community_signal_enrichment_wiring",
        "temporal_stability_integration_check",
        "permission_scope_integration_check",
        "tool_description_safety_enrichment_integration",
        "supply_chain_enrichment_synthesiser_wiring"
    ]
    
    sentinel_path = "/home/workspace/zo_sentinel"
    missing = []
    for module in wiring_modules:
        module_path = os.path.join(sentinel_path, f"{module}.py")
        if not os.path.exists(module_path):
            missing.append(module)
    
    if missing:
        result["errors"].append(f"Missing wiring modules: {missing}")
        log.error(f"Missing wiring: {missing}")
    else:
        log.info(f"All {len(wiring_modules)} wiring modules found")
        result["checks"].append(check_name)


def check_signal_analyser_integration(result: Dict[str, Any]):
    check_name = "signal_analyser_integration"
    log.info(f"Running check: {check_name}")
    
    signal_analyser_path = "/home/workspace/zo_sentinel/signal_analyser.py"
    if not os.path.exists(signal_analyser_path):
        result["errors"].append("signal_analyser.py not found")
        log.error("signal_analyser.py not found")
        return
    
    with open(signal_analyser_path, 'r') as f:
        content = f.read()
    
    required_imports = [
        "supply_chain",
        "community_signal",
        "temporal_stability",
        "permission_scope",
        "tool_description_safety",
        "domain_trust"
    ]
    
    missing_imports = []
    for imp in required_imports:
        if imp not in content:
            missing_imports.append(imp)
    
    if missing_imports:
        result["errors"].append(f"signal_analyser missing imports: {missing_imports}")
        log.error(f"Missing imports in signal_analyser: {missing_imports}")
    else:
        log.info("signal_analyser has all required enrichment imports")
        result["checks"].append(check_name)


def check_service_health_entries(result: Dict[str, Any]):
    check_name = "service_health_entries"
    log.info(f"Running check: {check_name}")
    
    rows = ws_query("SELECT service, last_heartbeat FROM service_health")
    if rows is None:
        result["errors"].append("Could not query service_health table")
        log.error("Failed to query service_health")
        return
    
    expected_services = [
        "signal_analyser",
        "mcp_scanner",
        "trust_synthesiser_v2",
        "attestation_engine",
        "rug_pull_monitor"
    ]
    
    found_services = {row.get("service") for row in rows if row.get("service")}
    missing_services = [s for s in expected_services if s not in found_services]
    
    if missing_services:
        log.warning(f"Expected services not in health table: {missing_services}")
    else:
        log.info(f"All expected services have heartbeat entries")
    
    result["checks"].append(check_name)


def check_mcp_signal_enrichments_table(result: Dict[str, Any]):
    check_name = "mcp_signal_enrichments_table"
    log.info(f"Running check: {check_name}")
    
    columns = ws_query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'mcp_signal_enrichments' ORDER BY ordinal_position"
    )
    
    if columns is None:
        result["errors"].append("mcp_signal_enrichments table not found or not queryable")
        log.error("Failed to query mcp_signal_enrichments schema")
        return
    
    required_columns = [
        "server_id",
        "signal_type",
        "score",
        "evidence",
        "computed_at"
    ]
    
    column_names = [col.get("column_name") for col in columns]
    missing = [c for c in required_columns if c not in column_names]
    
    if missing:
        result["errors"].append(f"mcp_signal_enrichments missing columns: {missing}")
        log.error(f"Missing columns: {missing}")
    else:
        log.info(f"mcp_signal_enrichments table has all required columns")
        result["checks"].append(check_name)


def check_write_service_connectivity(result: Dict[str, Any]):
    check_name = "write_service_connectivity"
    log.info(f"Running check: {check_name}")
    
    try:
        health_resp = requests.get("http://localhost:8772/health", timeout=5.0)
        if health_resp.status_code == 200:
            log.info("WriteService health check passed")
            result["checks"].append(check_name)
        else:
            result["errors"].append(f"WriteService health returned status {health_resp.status_code}")
            log.error(f"WriteService health check failed: {health_resp.status_code}")
    except Exception as e:
        result["errors"].append(f"WriteService connectivity failed: {e}")
        log.error(f"WriteService connectivity failed: {e}")


def check_enrichment_coverage(result: Dict[str, Any]):
    check_name = "enrichment_coverage"
    log.info(f"Running check: {check_name}")
    
    total_servers = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
    if total_servers is None:
        result["errors"].append("Could not count mcp_server_registry")
        return
    
    total_count = total_servers[0].get("cnt", 0) if total_servers else 0
    
    enriched_servers = ws_query(
        "SELECT COUNT(DISTINCT server_id) as cnt FROM mcp_signal_enrichments"
    )
    if enriched_servers is None:
        result["errors"].append("Could not count enriched servers")
        return
    
    enriched_count = enriched_servers[0].get("cnt", 0) if enriched_servers else 0
    
    coverage_pct = (enriched_count / total_count * 100) if total_count > 0 else 0
    
    log.info(f"Enrichment coverage: {enriched_count}/{total_count} ({coverage_pct:.1f}%)")
    
    if coverage_pct >= 50:
        result["checks"].append(check_name)
    else:
        result["errors"].append(f"Enrichment coverage too low: {coverage_pct:.1f}% (expected >= 50%)")
        log.warning(f"Enrichment coverage below threshold: {coverage_pct:.1f}%")


def check_signal_scores_integration(result: Dict[str, Any]):
    check_name = "signal_scores_integration"
    log.info(f"Running check: {check_name}")
    
    signal_types = ws_query(
        "SELECT DISTINCT signal_type FROM mcp_signal_enrichments ORDER BY signal_type"
    )
    
    if signal_types is None:
        result["errors"].append("Could not query signal types from mcp_signal_enrichments")
        return
    
    expected_signals = [
        "supply_chain",
        "community_signal",
        "temporal_stability",
        "permission_scope",
        "tool_description_safety",
        "domain_trust",
        "context_efficiency",
        "evidence_density",
        "registry_breadth",
        "vendor_concentration",
        "injection_resilience"
    ]
    
    found_signals = [s.get("signal_type") for s in signal_types]
    
    missing_signals = [s for s in expected_signals if s not in found_signals]
    
    if missing_signals:
        log.warning(f"Missing signal types in enrichments: {missing_signals}")
    
    log.info(f"Found {len(found_signals)} signal types in mcp_signal_enrichments")
    result["checks"].append(check_name)


def send_heartbeat():
    row = {
        "service": SERVICE_NAME,
        "status": "running",
        "ts": utc_now_iso(),
        "meta": "enrichment_pipeline_daemon_smoke_verification"
    }
    ws_write("service_health", [row])


def run():
    if not check_single_instance():
        log.error("Cannot run - another instance is active")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log.info("Starting enrichment pipeline daemon smoke verification")
    
    try:
        result = check_enrichment_pipeline_daemon()
        check_enrichment_coverage(result)
        check_signal_scores_integration(result)
        
        send_heartbeat()
        
        log.info("=" * 60)
        log.info(f"SMOKE VERIFICATION RESULT: {'PASS' if result['passed'] else 'FAIL'}")
        log.info(f"Checks passed: {len(result['checks'])}")
        log.info(f"Errors: {len(result['errors'])}")
        if result["errors"]:
            for err in result["errors"]:
                log.error(f"  - {err}")
        log.info("=" * 60)
        
    finally:
        remove_pid_file()
    
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    run()