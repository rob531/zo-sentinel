import os
import sys
import logging
import requests
import hashlib
from datetime import datetime, timezone
from pathlib import Path

# Service identity
SERVICE_NAME = "gate_scheduler_staleness_diagnostic"
SERVICE_VERSION = "1.0.0"
WRITE_SERVICE_URL = "http://localhost:8772"

# Paths
LOG_PATH = "/home/workspace/logs/gate_scheduler_staleness_diagnostic.log"
PID_FILE = "/home/workspace/zo_sentinel/gate_scheduler.pid"
TARGET_SERVICE = "gate_scheduler"

# Thresholds
HEARTBEAT_AGE_THRESHOLD_SECONDS = 180
STALENESS_WARNING_THRESHOLD_SECONDS = 300

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(SERVICE_NAME)


def ws_query(sql: str, params: tuple = None):
    """Query DuckDB via write_service"""
    payload = {
        "sql": sql,
        "wait": True
    }
    if params:
        payload["params"] = params
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json=payload,
            timeout=10
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get("rows", [])
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: list):
    """Write to DuckDB via write_service"""
    payload = {
        "table": table,
        "rows": rows,
        "wait": True
    }
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json=payload,
            timeout=10
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"ws_write failed for table {table}: {e}")
        raise


def check_write_service_connectivity():
    """Verify write_service is reachable"""
    try:
        resp = requests.get(f"{WRITE_SERVICE_URL}/health", timeout=5)
        if resp.status_code == 200:
            return {"reachable": True, "status": "healthy"}
    except Exception:
        pass
    # Fallback: try a minimal query
    try:
        ws_query("SELECT 1")
        return {"reachable": True, "status": "query_responsive"}
    except Exception as e:
        return {"reachable": False, "status": str(e)}


def get_latest_heartbeat(service_name: str):
    """Get the most recent heartbeat for a service"""
    sql = """
    SELECT last_heartbeat, status, ts, meta
    FROM service_health
    WHERE service_name = ?
    ORDER BY ts DESC
    LIMIT 1
    """
    rows = ws_query(sql, (service_name,))
    if rows:
        return rows[0]
    return None


def get_heartbeat_history(service_name: str, limit: int = 10):
    """Get heartbeat history for pattern analysis"""
    sql = """
    SELECT last_heartbeat, status, ts
    FROM service_health
    WHERE service_name = ?
    ORDER BY ts DESC
    LIMIT ?
    """
    return ws_query(sql, (service_name, limit))


def check_process_alive_via_pid():
    """Check if gate_scheduler process is alive via PID file"""
    if not os.path.exists(PID_FILE):
        return {"alive": False, "reason": "pid_file_missing", "pid": None}
    
    try:
        with open(PID_FILE, 'r') as f:
            pid_str = f.read().strip()
            pid = int(pid_str)
    except (ValueError, IOError) as e:
        return {"alive": False, "reason": f"pid_read_error: {e}", "pid": None}
    
    # Check if process exists
    try:
        os.kill(pid, 0)
        return {"alive": True, "pid": pid, "reason": "process_running"}
    except OSError:
        return {"alive": False, "reason": "process_not_running", "pid": pid}


def compute_heartbeat_age(last_heartbeat: str):
    """Compute age in seconds from ISO timestamp"""
    try:
        hb_time = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        age_seconds = (now - hb_time).total_seconds()
        return int(age_seconds)
    except Exception as e:
        logger.error(f"Failed to parse heartbeat timestamp: {e}")
        return None


def check_service_health_schema():
    """Verify service_health table schema"""
    sql = """
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = 'service_health'
    ORDER BY ordinal_position
    """
    return ws_query(sql)


def run_diagnostic():
    """Run the staleness diagnostic"""
    findings = {
        "service_name": TARGET_SERVICE,
        "diagnostic_ts": datetime.now(timezone.utc).isoformat(),
        "checks": {}
    }
    
    # Check 1: Write service connectivity
    logger.info("Checking write_service connectivity...")
    ws_status = check_write_service_connectivity()
    findings["checks"]["write_service"] = ws_status
    if not ws_status["reachable"]:
        logger.error("Write service not reachable - aborting diagnostic")
        findings["error"] = "write_service_unreachable"
        report_diagnostic(findings)
        return findings
    
    # Check 2: Schema verification
    logger.info("Verifying service_health schema...")
    schema = check_service_health_schema()
    findings["checks"]["schema"] = {"columns": schema}
    logger.info(f"service_health columns: {[c['column_name'] for c in schema]}")
    
    # Check 3: Process alive via PID
    logger.info("Checking gate_scheduler process via PID file...")
    process_status = check_process_alive_via_pid()
    findings["checks"]["process"] = process_status
    logger.info(f"Process status: {process_status}")
    
    # Check 4: Heartbeat analysis
    logger.info("Analyzing heartbeat pattern...")
    latest_hb = get_latest_heartbeat(TARGET_SERVICE)
    hb_history = get_heartbeat_history(TARGET_SERVICE, 10)
    
    if latest_hb:
        age_seconds = compute_heartbeat_age(latest_hb["last_heartbeat"])
        is_stale = age_seconds and age_seconds > STALENESS_WARNING_THRESHOLD_SECONDS
        is_warning = age_seconds and age_seconds > HEARTBEAT_AGE_THRESHOLD_SECONDS
        
        findings["checks"]["heartbeat"] = {
            "latest": latest_hb,
            "age_seconds": age_seconds,
            "is_stale": is_stale,
            "is_warning": is_warning,
            "threshold_seconds": HEARTBEAT_AGE_THRESHOLD_SECONDS,
            "staleness_threshold_seconds": STALENESS_WARNING_THRESHOLD_SECONDS,
            "history_count": len(hb_history),
            "history": hb_history
        }
        logger.info(f"Latest heartbeat age: {age_seconds}s (stale={is_stale}, warning={is_warning})")
    else:
        findings["checks"]["heartbeat"] = {
            "latest": None,
            "error": "no_heartbeat_found",
            "history_count": 0
        }
        logger.warning("No heartbeat found for gate_scheduler")
    
    # Check 5: Staleness determination
    heartbeat_ok = (
        latest_hb and 
        compute_heartbeat_age(latest_hb["last_heartbeat"]) <= HEARTBEAT_AGE_THRESHOLD_SECONDS
    )
    process_ok = process_status["alive"]
    
    findings["staleness_detected"] = not heartbeat_ok
    findings["recommendation"] = determine_recommendation(findings)
    
    logger.info(f"Diagnostic complete. Stale={findings['staleness_detected']}")
    logger.info(f"Recommendation: {findings['recommendation']}")
    
    report_diagnostic(findings)
    return findings


def determine_recommendation(findings: dict) -> str:
    """Determine recommendation based on findings"""
    checks = findings.get("checks", {})
    process_alive = checks.get("process", {}).get("alive", False)
    heartbeat = checks.get("heartbeat", {})
    latest_hb = heartbeat.get("latest")
    
    if not latest_hb:
        if not process_alive:
            return "CRITICAL: No heartbeat and process not running. Manual intervention required."
        return "WARNING: No heartbeat recorded yet. Process running. Monitor."
    
    age_seconds = heartbeat.get("age_seconds", 0)
    
    if age_seconds > STALENESS_WARNING_THRESHOLD_SECONDS and not process_alive:
        return "CRITICAL: Heartbeat stale and process dead. Restart gate_scheduler immediately."
    
    if age_seconds > STALENESS_WARNING_THRESHOLD_SECONDS:
        return "WARNING: Heartbeat exceeds staleness threshold. Investigate gate_scheduler health."
    
    if age_seconds > HEARTBEAT_AGE_THRESHOLD_SECONDS:
        return "CAUTION: Heartbeat age above threshold but within acceptable range. Monitor closely."
    
    return "OK: gate_scheduler appears healthy. Heartbeat within normal bounds."


def report_diagnostic(findings: dict):
    """Report findings to service_diagnostics table"""
    diag_row = {
        "diagnostic_service": SERVICE_NAME,
        "target_service": TARGET_SERVICE,
        "diagnostic_ts": findings["diagnostic_ts"],
        "target_stale": findings.get("staleness_detected", None),
        "process_alive": findings["checks"].get("process", {}).get("alive", None),
        "process_pid": findings["checks"].get("process", {}).get("pid", None),
        "heartbeat_age_seconds": findings["checks"].get("heartbeat", {}).get("age_seconds", None),
        "heartbeat_last_ts": findings["checks"].get("heartbeat", {}).get("latest", {}).get("ts", None),
        "heartbeat_last_heartbeat": findings["checks"].get("heartbeat", {}).get("latest", {}).get("last_heartbeat", None),
        "recommendation": findings.get("recommendation", ""),
        "full_report_json": str(findings)
    }
    
    try:
        ws_write("service_diagnostics", [diag_row])
        logger.info("Diagnostic report written to service_diagnostics")
    except Exception as e:
        logger.error(f"Failed to write diagnostic report: {e}")


if __name__ == "__main__":
    logger.info(f"{SERVICE_NAME} v{SERVICE_VERSION} starting...")
    try:
        results = run_diagnostic()
        if results.get("error") == "write_service_unreachable":
            logger.error("Aborted due to write_service unreachability")
            sys.exit(1)
        logger.info("Diagnostic completed successfully")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Diagnostic failed: {e}")
        sys.exit(1)