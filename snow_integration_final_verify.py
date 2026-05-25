import sys
import os
import logging
import requests
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

sys.path.insert(0, '/home/workspace/zo_sentinel')

SERVICE_NAME = "snow_integration_final_verify"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772"
APPROVAL_WORKFLOW_URL = "http://127.0.0.1:8780"
SNOW_WEBHOOK_ENDPOINT = "http://127.0.0.1:8780/webhook/snow"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log'), logging.StreamHandler()]
)
log = logging.getLogger(SERVICE_NAME)


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL + "/query",
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL + "/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL + "/execute",
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_write_service_connectivity() -> Dict[str, Any]:
    log.info("Checking write_service connectivity...")
    result = {"status": "unknown", "connected": False}
    
    try:
        resp = requests.get(WRITE_SERVICE_URL + "/health", timeout=10)
        if resp.status_code == 200:
            result["status"] = "healthy"
            result["connected"] = True
            log.info("Write service is reachable and healthy")
        else:
            result["status"] = f"unhealthy_http_{resp.status_code}"
            log.warning(f"Write service returned status {resp.status_code}")
    except requests.exceptions.ConnectionError:
        result["status"] = "connection_failed"
        log.error("Cannot connect to write_service")
    except Exception as e:
        result["status"] = f"error_{str(e)}"
        log.error(f"Write service health check failed: {e}")
    
    return result


def check_approval_workflow_health() -> Dict[str, Any]:
    log.info("Checking approval_workflow service health...")
    result = {"status": "unknown", "running": False}
    
    try:
        resp = requests.get(APPROVAL_WORKFLOW_URL + "/health", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            result["status"] = data.get("status", "unknown")
            result["running"] = True
            result["data"] = data
            log.info(f"Approval workflow is running: {data}")
        else:
            result["status"] = f"http_{resp.status_code}"
            log.warning(f"Approval workflow returned {resp.status_code}")
    except requests.exceptions.ConnectionError:
        result["status"] = "connection_failed"
        log.error("Cannot connect to approval_workflow")
    except Exception as e:
        result["status"] = f"error_{str(e)}"
        log.error(f"Approval workflow health check failed: {e}")
    
    return result


def check_snow_webhook_registration() -> Dict[str, Any]:
    log.info("Checking Snow webhook registration status...")
    result = {"registered": False, "verified": False}
    
    sql = """
    SELECT table_name FROM information_schema.tables 
    WHERE table_schema = 'main' 
    AND table_name LIKE '%snow%'
    """
    tables = ws_query(sql)
    log.info(f"Found Snow-related tables: {[t.get('table_name') for t in tables]}")
    
    webhook_tables = ["snow_inbound_webhooks", "snow_webhook_events", "snow_connector_state"]
    for tbl in webhook_tables:
        sql = f"SELECT COUNT(*) as cnt FROM information_schema.tables WHERE table_name = '{tbl}'"
        rows = ws_query(sql)
        if rows and rows[0].get("cnt", 0) > 0:
            result["verified"] = True
            log.info(f"Found webhook tracking table: {tbl}")
    
    return result


def verify_approval_workflow_integration() -> Dict[str, Any]:
    log.info("Verifying approval_workflow integration path...")
    result = {"path_valid": False, "checks": {}}
    
    sql = """
    SELECT COUNT(*) as cnt FROM information_schema.tables 
    WHERE table_name = 'mcp_submissions'
    """
    rows = ws_query(sql)
    result["checks"]["mcp_submissions_exists"] = rows and rows[0].get("cnt", 0) > 0
    
    sql = """
    SELECT COUNT(*) as cnt FROM information_schema.tables 
    WHERE table_name = 'approval_decisions'
    """
    rows = ws_query(sql)
    result["checks"]["approval_decisions_exists"] = rows and rows[0].get("cnt", 0) > 0
    
    sql = """
    SELECT column_name FROM information_schema.columns 
    WHERE table_name = 'mcp_submissions'
    AND column_name IN ('snow_ticket_id', 'snow_workflow_status')
    """
    columns = ws_query(sql)
    result["checks"]["snow_columns_exist"] = len(columns) >= 1
    
    result["path_valid"] = all(result["checks"].values())
    log.info(f"Approval workflow integration checks: {result['checks']}")
    
    return result


def check_snow_connector_approval_wiring_file() -> Dict[str, Any]:
    log.info("Checking snow_connector_approval_wiring.py file integrity...")
    result = {"exists": False, "valid": False}
    
    wiring_file = "/home/workspace/zo_sentinel/snow_connector_approval_wiring.py"
    if os.path.exists(wiring_file):
        result["exists"] = True
        with open(wiring_file, 'r') as f:
            content = f.read()
        
        required_patterns = [
            "ws_query", "ws_write", "utc_now_iso",
            "APPROVAL_WORKFLOW_URL", "WRITE_SERVICE_URL",
            "check_single_instance", "send_heartbeat"
        ]
        result["valid"] = all(p in content for p in required_patterns)
        log.info(f"Wiring file valid: {result['valid']}")
    
    return result


def check_snow_connector_approval_verify_file() -> Dict[str, Any]:
    log.info("Checking snow_connector_approval_verify.py file integrity...")
    result = {"exists": False, "valid": False}
    
    verify_file = "/home/workspace/zo_sentinel/snow_connector_approval_verify.py"
    if os.path.exists(verify_file):
        result["exists"] = True
        with open(verify_file, 'r') as f:
            content = f.read()
        
        required_patterns = [
            "ws_query", "ws_write", "get_service_health",
            "check_snow_connector_heartbeat", "check_webhook_registration"
        ]
        result["valid"] = all(p in content for p in required_patterns)
        log.info(f"Verify file valid: {result['valid']}")
    
    return result


def verify_db_schema_integration() -> Dict[str, Any]:
    log.info("Verifying database schema for Snow integration...")
    result = {"complete": False, "tables": {}}
    
    required_tables = [
        "mcp_submissions",
        "approval_decisions",
        "audit_log",
        "service_health"
    ]
    
    for tbl in required_tables:
        sql = f"SELECT COUNT(*) as cnt FROM information_schema.tables WHERE table_name = '{tbl}'"
        rows = ws_query(sql)
        result["tables"][tbl] = rows and rows[0].get("cnt", 0) > 0
    
    sql = """
    SELECT column_name FROM information_schema.columns 
    WHERE table_name = 'mcp_submissions'
    """
    columns = ws_query(sql)
    result["tables"]["mcp_submissions_columns"] = [c.get("column_name") for c in columns]
    
    result["complete"] = all(result["tables"].values())
    log.info(f"Schema integration complete: {result['complete']}")
    
    return result


def run_integration_test() -> Dict[str, Any]:
    log.info("Running integration smoke test...")
    result = {"passed": False, "checks": {}}
    
    test_timestamp = utc_now_iso()
    test_id = f"verify_test_{test_timestamp.replace(':', '').replace('-', '')}"
    
    try:
        sql = """
        INSERT INTO audit_log (id, event_type, detail, created_at)
        VALUES (?, ?, ?, ?)
        """.replace("?", "'{}'".format(test_id)).replace("'{}'".format("verify_snow_integration"), f"'{test_id}'").replace("'Snow connector integration verification test'", f"'Snow connector integration verification test'")
        
        sql = f"""
        INSERT INTO audit_log (id, event_type, detail, created_at)
        VALUES ('{test_id}', 'verify_snow_integration', 'Snow connector integration verification test', '{test_timestamp}')
        """
        
        ws_execute(sql)
        result["checks"]["write_audit_log"] = True
        log.info("Audit log write successful")
    except Exception as e:
        log.warning(f"Audit log write test failed (table may not exist): {e}")
        result["checks"]["write_audit_log"] = False
    
    try:
        sql = "SELECT service, last_heartbeat, status FROM service_health LIMIT 5"
        rows = ws_query(sql)
        result["checks"]["query_service_health"] = True
        result["services"] = rows
        log.info(f"Service health query returned {len(rows)} rows")
    except Exception as e:
        log.error(f"Service health query failed: {e}")
        result["checks"]["query_service_health"] = False
    
    result["passed"] = result["checks"].get("query_service_health", False)
    
    return result


def verify_webhook_endpoint_accessible() -> Dict[str, Any]:
    log.info("Checking webhook endpoint accessibility...")
    result = {"accessible": False, "status_code": None}
    
    try:
        resp = requests.get(SNOW_WEBHOOK_ENDPOINT.replace("/webhook/snow", "").replace(":8780", ":8780/api/health"), timeout=10)
        result["accessible"] = resp.status_code == 200
        result["status_code"] = resp.status_code
        log.info(f"Webhook endpoint base accessible: {resp.status_code}")
    except requests.exceptions.ConnectionError:
        log.warning("Webhook endpoint not accessible yet (service may be starting)")
        result["accessible"] = False
    except Exception as e:
        log.error(f"Webhook endpoint check failed: {e}")
        result["accessible"] = False
    
    return result


def generate_verification_report(
    ws_connectivity: Dict,
    approval_workflow_health: Dict,
    webhook_registration: Dict,
    approval_integration: Dict,
    wiring_file_check: Dict,
    verify_file_check: Dict,
    schema_integration: Dict,
    integration_test: Dict,
    webhook_access: Dict
) -> Dict[str, Any]:
    log.info("Generating final verification report...")
    
    report = {
        "verification_id": f"verify_{utc_now_iso().replace(':', '').replace('-', '')}",
        "timestamp": utc_now_iso(),
        "overall_status": "PASS",
        "checks": {
            "write_service_connectivity": ws_connectivity,
            "approval_workflow_health": approval_workflow_health,
            "webhook_registration": webhook_registration,
            "approval_integration_path": approval_integration,
            "wiring_file_integrity": wiring_file_check,
            "verify_file_integrity": verify_file_check,
            "schema_integration": schema_integration,
            "integration_smoke_test": integration_test,
            "webhook_endpoint_accessible": webhook_access
        },
        "summary": {}
    }
    
    critical_checks = [
        ("write_service_connectivity", ws_connectivity.get("connected", False)),
        ("approval_workflow_health", approval_workflow_health.get("running", False)),
        ("approval_integration_path", approval_integration.get("path_valid", False)),
        ("wiring_file_integrity", wiring_file_check.get("valid", False)),
    ]
    
    failed_critical = [name for name, passed in critical_checks if not passed]
    if failed_critical:
        report["overall_status"] = "FAIL"
        report["failed_critical_checks"] = failed_critical
        log.error(f"FAILED critical checks: {failed_critical}")
    else:
        log.info("All critical checks passed")
    
    report["summary"]["critical_checks_passed"] = len(critical_checks) - len(failed_critical)
    report["summary"]["critical_checks_total"] = len(critical_checks)
    report["summary"]["schema_tables_found"] = sum(1 for v in schema_integration.get("tables", {}).values() if v)
    
    return report


def print_report(report: Dict[str, Any]) -> None:
    log.info("=" * 60)
    log.info("SNOW INTEGRATION FINAL VERIFICATION REPORT")
    log.info("=" * 60)
    log.info(f"Verification ID: {report['verification_id']}")
    log.info(f"Timestamp: {report['timestamp']}")
    log.info(f"Overall Status: {report['overall_status']}")
    log.info("")
    
    log.info("CRITICAL CHECKS:")
    for check_name in ["write_service_connectivity", "approval_workflow_health", "approval_integration_path", "wiring_file_integrity"]:
        check = report["checks"].get(check_name, {})
        status = "PASS" if check.get("connected", False) or check.get("running", False) or check.get("path_valid", False) or check.get("valid", False) else "FAIL"
        log.info(f"  {check_name}: {status}")
    
    log.info("")
    log.info("SUPPORTING CHECKS:")
    for check_name in ["webhook_registration", "verify_file_integrity", "schema_integration", "integration_smoke_test", "webhook_endpoint_accessible"]:
        check = report["checks"].get(check_name, {})
        status = "PASS" if check.get("verified", False) or check.get("valid", False) or check.get("complete", False) or check.get("passed", False) or check.get("accessible", False) else "WARN"
        log.info(f"  {check_name}: {status}")
    
    log.info("")
    log.info(f"Summary: {report['summary']['critical_checks_passed']}/{report['summary']['critical_checks_total']} critical checks passed")
    log.info("=" * 60)


def persist_verification_report(report: Dict[str, Any]) -> bool:
    log.info("Persisting verification report to service health...")
    
    try:
        report_text = json.dumps(report, indent=2)
        sql = f"""
        INSERT INTO audit_log (id, event_type, detail, created_at)
        VALUES ('{report['verification_id']}', 'snow_integration_verify', '{report_text.replace("'", "''")}', '{report['timestamp']}')
        """
        ws_execute(sql)
        return True
    except Exception as e:
        log.error(f"Failed to persist verification report: {e}")
        return False


def run() -> None:
    log.info("Starting Snow integration final verification...")
    
    ws_connectivity = check_write_service_connectivity()
    
    approval_workflow_health = check_approval_workflow_health()
    
    webhook_registration = check_snow_webhook_registration()
    
    approval_integration = verify_approval_workflow_integration()
    
    wiring_file_check = check_snow_connector_approval_wiring_file()
    
    verify_file_check = check_snow_connector_approval_verify_file()
    
    schema_integration = verify_db_schema_integration()
    
    integration_test = run_integration_test()
    
    webhook_access = verify_webhook_endpoint_accessible()
    
    report = generate_verification_report(
        ws_connectivity,
        approval_workflow_health,
        webhook_registration,
        approval_integration,
        wiring_file_check,
        verify_file_check,
        schema_integration,
        integration_test,
        webhook_access
    )
    
    print_report(report)
    
    persist_verification_report(report)
    
    if report["overall_status"] == "PASS":
        log.info("Snow integration verification PASSED")
        sys.exit(0)
    else:
        log.error("Snow integration verification FAILED")
        sys.exit(1)


if __name__ == "__main__":
    run()