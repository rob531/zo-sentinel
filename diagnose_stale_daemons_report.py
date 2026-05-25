import logging
import requests
import os
import signal
from datetime import datetime, timedelta
from typing import Optional

SERVICE_NAME = "diagnose_stale_daemons_report"
SERVICE_PORT = 8772
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
INFERENCE_ROUTER_URL = "http://127.0.0.1:8773/route"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(SERVICE_NAME)


def ws_query(query: str) -> list:
    response = requests.post(
        WRITE_SERVICE_URL,
        json={"table": "_internal_query", "rows": {"query": query}, "wait": True},
        timeout=30
    )
    response.raise_for_status()
    return response.json().get("results", [])


def ws_write(table: str, row: dict, wait: bool = True) -> dict:
    response = requests.post(
        WRITE_SERVICE_URL,
        json={"table": table, "rows": row, "wait": wait},
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def check_process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def get_pid_from_file(pid_file_path: str) -> Optional[int]:
    if os.path.exists(pid_file_path):
        try:
            with open(pid_file_path, 'r') as f:
                return int(f.read().strip())
        except (ValueError, IOError):
            return None
    return None


def diagnose_stale_daemons() -> dict:
    log.info("Fetching service_health data from DuckDB...")
    
    query = "SELECT service, last_heartbeat, status FROM service_health ORDER BY last_heartbeat ASC"
    try:
        results = ws_query(query)
    except Exception as e:
        log.error(f"Failed to query service_health: {e}")
        return {"error": str(e)}
    
    services_of_interest = [
        "write_service", 
        "mcp_scanner", 
        "rug_pull_monitor", 
        "anti_entropy", 
        "wisdom_synthesiser"
    ]
    
    now = datetime.utcnow()
    stale_threshold = timedelta(hours=1)
    critical_threshold = timedelta(hours=24)
    
    report = {
        "generated_at": now.isoformat(),
        "services": {},
        "summary": {
            "healthy": 0,
            "slow": 0,
            "stale": 0,
            "dead": 0
        }
    }
    
    for row in results:
        service = row.get("service", "")
        if service not in services_of_interest:
            continue
        
        last_heartbeat_str = row.get("last_heartbeat", "")
        status = row.get("status", "unknown")
        
        try:
            last_heartbeat = datetime.fromisoformat(last_heartbeat_str.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            last_heartbeat = None
        
        service_report = {
            "service": service,
            "last_heartbeat": last_heartbeat_str,
            "status": status,
            "classification": "unknown",
            "pid_check": None,
            "process_running": None,
            "details": []
        }
        
        if last_heartbeat:
            last_heartbeat_utc = last_heartbeat.replace(tzinfo=None) if last_heartbeat.tzinfo else last_heartbeat
            age = now - last_heartbeat_utc
            age_hours = age.total_seconds() / 3600
            
            service_report["age_hours"] = round(age_hours, 2)
            
            if age > critical_threshold:
                service_report["classification"] = "dead"
                service_report["details"].append(f"CRITICAL: No heartbeat for {age_hours:.1f} hours")
                report["summary"]["dead"] += 1
            elif age > stale_threshold:
                service_report["classification"] = "stale"
                service_report["details"].append(f"WARNING: No heartbeat for {age_hours:.1f} hours")
                report["summary"]["stale"] += 1
            else:
                service_report["classification"] = "healthy"
                report["summary"]["healthy"] += 1
        
        if service == "rug_pull_monitor":
            log.info("Checking rug_pull_monitor PID file...")
            pid_file_paths = [
                "/home/workspace/zo_sentinel/rug_pull_monitor.pid",
                "/var/run/zo_sentinel/rug_pull_monitor.pid",
                "/tmp/rug_pull_monitor.pid"
            ]
            
            found_pid = None
            for pid_path in pid_file_paths:
                pid = get_pid_from_file(pid_path)
                if pid:
                    found_pid = pid
                    service_report["pid_file"] = pid_path
                    break
            
            if found_pid:
                service_report["pid_check"] = found_pid
                process_running = check_process_exists(found_pid)
                service_report["process_running"] = process_running
                
                if not process_running:
                    service_report["details"].append(f"DEAD: PID {found_pid} file exists but process not running")
                    if service_report["classification"] != "dead":
                        service_report["classification"] = "dead"
                        report["summary"]["stale"] -= 1
                        report["summary"]["dead"] += 1
                else:
                    service_report["details"].append(f"ALIVE: Process running with PID {found_pid}")
            else:
                service_report["details"].append("PID file not found in standard locations")
        
        if service == "write_service" and status == "error":
            service_report["details"].append("Service reports ERROR status")
            service_report["classification"] = "dead"
        
        report["services"][service] = service_report
    
    log.info(f"Diagnostic summary: {report['summary']}")
    
    return report


def send_heartbeat(service: str = SERVICE_NAME, status: str = "running"):
    try:
        ws_write(
            "service_health",
            {
                "service": service,
                "last_heartbeat": datetime.utcnow().isoformat() + "Z",
                "status": status
            }
        )
    except Exception as e:
        log.warning(f"Failed to send heartbeat: {e}")


def run():
    log.info("Starting diagnose_stale_daemons_report service...")
    
    send_heartbeat(status="running")
    
    report = diagnose_stale_daemons()
    
    log.info("=== DIAGNOSTIC REPORT ===")
    log.info(f"Generated at: {report['generated_at']}")
    log.info("")
    
    for service, info in report["services"].items():
        log.info(f"--- {service} ---")
        log.info(f"  Classification: {info['classification']}")
        log.info(f"  Last heartbeat: {info.get('last_heartbeat', 'N/A')}")
        if "age_hours" in info:
            log.info(f"  Age: {info['age_hours']} hours")
        if info.get("pid_check"):
            log.info(f"  PID: {info['pid_check']}")
            log.info(f"  Process running: {info['process_running']}")
        for detail in info.get("details", []):
            log.info(f"  {detail}")
        log.info("")
    
    log.info(f"Summary: {report['summary']}")
    
    ws_write(
        "diagnostic_reports",
        {
            "report_type": "stale_daemons",
            "generated_at": report["generated_at"],
            "summary": str(report["summary"]),
            "services_classified": ",".join([f"{k}:{v['classification']}" for k, v in report["services"].items()])
        }
    )
    
    send_heartbeat(status="completed")
    log.info("Diagnostic report completed.")


if __name__ == "__main__":
    run()