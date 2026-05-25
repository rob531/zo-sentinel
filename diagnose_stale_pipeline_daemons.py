import time
import requests
import os
import signal
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

SERVICE_NAME = "diagnose_stale_pipeline_daemons"
SERVICE_PORT = 8786
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
EXECUTE_URL = f"{WRITE_SERVICE_URL}/execute"
HEARTBEAT_INTERVAL = 60
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"

STALE_DAEMONS = {
    "mcp_scanner": 143 * 60 + 26,
    "signal_analyser": 141 * 60 + 27,
    "trust_synthesiser": 141 * 60 + 52,
    "threat_intel_ingestor": 143 * 60 + 3,
    "attestation_engine": 143 * 60 + 27,
    "risk_ranker": 143 * 60 + 27,
}

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"Could not write to log: {e}")

def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log(f"QUERY ERROR: {e}")
        return None

def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    try:
        resp = requests.post(WRITE_SERVICE_URL + "/write", json={"table": table, "rows": rows}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        log(f"WRITE ERROR: {e}")
        return False

def send_heartbeat() -> None:
    ws_write("service_health", {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.now(timezone.utc).isoformat()
    })

def check_write_service_connectivity() -> Dict[str, Any]:
    result = {
        "reachable": False,
        "response_time_ms": None,
        "status_code": None,
        "error": None
    }
    try:
        start = time.time()
        resp = requests.get(WRITE_SERVICE_URL + "/health", timeout=5)
        elapsed = (time.time() - start) * 1000
        result["reachable"] = True
        result["response_time_ms"] = round(elapsed, 2)
        result["status_code"] = resp.status_code
    except Exception as e:
        result["error"] = str(e)
    return result

def get_service_health_status() -> List[Dict[str, Any]]:
    sql = "SELECT service, last_heartbeat FROM service_health"
    return ws_query(sql) or []

def get_stale_service_report() -> List[Dict[str, Any]]:
    services = get_service_health_status()
    now = datetime.now(timezone.utc)
    stale_report = []
    
    for svc in services:
        svc_name = svc.get("service", "")
        heartbeat_str = svc.get("last_heartbeat", "")
        
        if not heartbeat_str:
            stale_report.append({
                "service": svc_name,
                "status": "NO_HEARTBEAT",
                "age_seconds": None,
                "is_stale": True
            })
            continue
        
        try:
            heartbeat_dt = datetime.fromisoformat(heartbeat_str.replace('Z', '+00:00'))
            age_seconds = (now - heartbeat_dt).total_seconds()
            is_stale = age_seconds > 300
            stale_report.append({
                "service": svc_name,
                "status": "STALE" if is_stale else "HEALTHY",
                "age_seconds": round(age_seconds, 1),
                "is_stale": is_stale,
                "last_heartbeat": heartbeat_str
            })
        except Exception as e:
            stale_report.append({
                "service": svc_name,
                "status": "PARSE_ERROR",
                "error": str(e),
                "is_stale": True
            })
    
    return stale_report

def check_daemon_processes() -> List[Dict[str, Any]]:
    process_info = []
    for daemon_name in STALE_DAEMONS:
        pid_file = f"/tmp/{daemon_name}.pid"
        info = {
            "daemon": daemon_name,
            "pid_file_exists": os.path.exists(pid_file),
            "pid": None,
            "is_running": False,
            "uptime_seconds": None,
            "stderr_tail": None
        }
        
        if info["pid_file_exists"]:
            try:
                with open(pid_file, "r") as f:
                    pid_str = f.read().strip()
                    if pid_str:
                        info["pid"] = int(pid_str)
                        try:
                            os.kill(info["pid"], 0)
                            info["is_running"] = True
                            proc_path = f"/proc/{info['pid']}"
                            if os.path.exists(proc_path):
                                stat_file = f"{proc_path}/stat"
                                if os.path.exists(stat_file):
                                    stat_mtime = os.path.getmtime(stat_file)
                                    info["uptime_seconds"] = round(time.time() - stat_mtime)
                        except (ProcessLookupError, PermissionError):
                            info["is_running"] = False
            except Exception as e:
                info["error"] = str(e)
        
        stderr_file = f"/tmp/{daemon_name}_stderr.log"
        if os.path.exists(stderr_file):
            try:
                with open(stderr_file, "r") as f:
                    lines = f.readlines()
                    info["stderr_tail"] = "".join(lines[-20:]) if len(lines) > 20 else "".join(lines)
            except Exception:
                pass
        
        process_info.append(info)
    
    return process_info

def check_mcp_registry_table() -> Dict[str, Any]:
    result = {"exists": False, "row_count": None, "error": None}
    
    try:
        rows = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
        if rows and len(rows) > 0:
            result["exists"] = True
            result["row_count"] = rows[0].get("cnt", 0)
    except Exception as e:
        result["error"] = str(e)
    
    return result

def check_signal_scores_table() -> Dict[str, Any]:
    result = {"exists": False, "row_count": None, "error": None}
    
    try:
        rows = ws_query("SELECT COUNT(*) as cnt FROM mcp_signal_scores")
        if rows and len(rows) > 0:
            result["exists"] = True
            result["row_count"] = rows[0].get("cnt", 0)
    except Exception as e:
        result["error"] = str(e)
    
    return result

def get_recent_audit_events() -> List[Dict[str, Any]]:
    sql = """
    SELECT id, target_server_id, event_type, actor, detail, created_at 
    FROM audit_log 
    ORDER BY created_at DESC 
    LIMIT 50
    """
    return ws_query(sql) or []

def check_write_service_errors() -> Dict[str, Any]:
    result = {"recent_errors": [], "can_connect": True}
    
    try:
        rows = ws_query("""
            SELECT * FROM service_health 
            WHERE service LIKE '%error%' 
            OR service LIKE '%fail%'
            LIMIT 10
        """)
        result["recent_errors"] = rows or []
    except Exception as e:
        result["can_connect"] = False
        result["error"] = str(e)
    
    return result

def generate_diagnostic_report() -> str:
    log("=== STALE PIPELINE DAEMON DIAGNOSTIC REPORT ===")
    log("")
    
    log("--- WRITE SERVICE CONNECTIVITY ---")
    ws_status = check_write_service_connectivity()
    log(f"Reachable: {ws_status['reachable']}")
    log(f"Response Time: {ws_status.get('response_time_ms', 'N/A')}ms")
    log(f"Status Code: {ws_status.get('status_code', 'N/A')}")
    log(f"Error: {ws_status.get('error', 'None')}")
    log("")
    
    log("--- STALE DAEMONS (target) ---")
    for name, approx_age in STALE_DAEMONS.items():
        log(f"  {name}: ~{approx_age}s stale")
    log("")
    
    log("--- SERVICE HEALTH STATUS ---")
    health_report = get_stale_service_report()
    for entry in health_report:
        status_marker = "***STALE***" if entry.get("is_stale") else "OK"
        age_str = f"{entry.get('age_seconds', 'N/A')}s" if entry.get('age_seconds') else "N/A"
        log(f"  [{status_marker}] {entry.get('service')}: {age_str} (status: {entry.get('status')})")
    log("")
    
    log("--- DAEMON PROCESS STATUS ---")
    process_info = check_daemon_processes()
    for pinfo in process_info:
        pid_status = "PID:" + str(pinfo.get('pid', 'N/A')) if pinfo.get('pid') else "NO_PID"
        run_status = "RUNNING" if pinfo.get('is_running') else "STOPPED"
        uptime_str = f"uptime:{pinfo.get('uptime_seconds', 'N/A')}s" if pinfo.get('uptime_seconds') else ""
        log(f"  {pinfo['daemon']}: {pid_status} {run_status} {uptime_str}")
        if pinfo.get('stderr_tail') and pinfo['stderr_tail'].strip():
            log(f"    STDERR snippet: {pinfo['stderr_tail'][:200]}...")
    log("")
    
    log("--- DATABASE TABLES ---")
    reg_info = check_mcp_registry_table()
    log(f"  mcp_server_registry: exists={reg_info['exists']}, rows={reg_info.get('row_count', 'N/A')}")
    sig_info = check_signal_scores_table()
    log(f"  mcp_signal_scores: exists={sig_info['exists']}, rows={sig_info.get('row_count', 'N/A')}")
    log("")
    
    log("--- RECENT AUDIT EVENTS ---")
    audit_rows = get_recent_audit_events()
    if audit_rows:
        log(f"  Found {len(audit_rows)} recent audit events")
        for row in audit_rows[:5]:
            log(f"    {row.get('event_type')}: {row.get('detail', '')[:80]}...")
    else:
        log("  No recent audit events found")
    log("")
    
    log("--- WRITE SERVICE ERROR CHECK ---")
    ws_errors = check_write_service_errors()
    log(f"  Can connect: {ws_errors.get('can_connect', False)}")
    if ws_errors.get('recent_errors'):
        log(f"  Found {len(ws_errors['recent_errors'])} error entries in service_health")
    log("")
    
    log("=== DIAGNOSTIC COMPLETE ===")
    
    report = f"""
STALE PIPELINE DAEMON DIAGNOSTIC REPORT
Generated: {datetime.now(timezone.utc).isoformat()}

SUMMARY:
- Write Service: {'REACHABLE' if ws_status['reachable'] else 'UNREACHABLE'} ({ws_status.get('response_time_ms', 'N/A')}ms)
- Stale Daemons Target: {list(STALE_DAEMONS.keys())}
- Services in service_health: {len(health_report)}
- Stale Services: {sum(1 for h in health_report if h.get('is_stale'))}

DETAILED FINDINGS:

1. WRITE SERVICE HEALTH:
   - Reachable: {ws_status['reachable']}
   - Response Time: {ws_status.get('response_time_ms', 'N/A')}ms
   - Status: {ws_status.get('status_code', 'N/A')}

2. TARGET STALE DAEMONS:
"""
    for name, age in STALE_DAEMONS.items():
        report += f"   - {name}: ~{age}s old\n"
    
    report += "\n3. SERVICE_HEALTH TABLE STATUS:\n"
    for entry in health_report:
        report += f"   - {entry.get('service')}: {entry.get('status')} ({entry.get('age_seconds', 'N/A')}s)\n"
    
    report += "\n4. PROCESS STATUS:\n"
    for pinfo in process_info:
        report += f"   - {pinfo['daemon']}: pid={pinfo.get('pid', 'N/A')}, running={pinfo.get('is_running', False)}\n"
    
    return report

def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            log(f"Already running with PID {pid}")
            return False
        except (ValueError, ProcessLookupError, PermissionError):
            log("Stale PID file found, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True

def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

def signal_handler(signum, frame) -> None:
    log(f"Received signal {signum}, shutting down")
    remove_pid_file()
    exit(0)

def run() -> None:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        log("Another instance is already running")
        return
    
    log("Starting stale pipeline daemon diagnostic")
    
    report = generate_diagnostic_report()
    
    print("\n" + "=" * 60)
    print("STALE PIPELINE DIAGNOSTIC COMPLETE")
    print("=" * 60)
    print(report)
    print(f"\nFull log available at: {LOG_FILE}")
    print(f"\nACTION REQUIRED: Review findings above.")
    print("NOTE: This diagnostic did NOT restart any daemons.")
    
    send_heartbeat()
    
    log("Diagnostic complete, exiting")

if __name__ == "__main__":
    run()