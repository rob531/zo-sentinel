#!/usr/bin/env python3
"""
Self-Diagnostics Stale Diagnostic Report
Checks daemon health, connectivity, and data state
"""

import requests
import time
import json
import os
import subprocess
from datetime import datetime, timedelta

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
OUTPUT_FILE = "diagnose_self_diagnostics_stale.py"

def write_service_query(sql):
    """Query write_service for data"""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": sql},
            timeout=10
        )
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def write_service_execute(sql):
    """Execute on write_service"""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/execute",
            json={"sql": sql},
            timeout=10
        )
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def check_write_service_connectivity():
    """Test write_service connectivity"""
    result = {"connected": False, "latency_ms": None, "error": None}
    try:
        start = time.time()
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": "SELECT 1 as test"},
            timeout=5
        )
        latency = (time.time() - start) * 1000
        result["latency_ms"] = round(latency, 2)
        result["connected"] = resp.status_code == 200
        if not result["connected"]:
            result["error"] = f"Status code: {resp.status_code}"
    except Exception as e:
        result["error"] = str(e)
    return result

def check_daemon_logs():
    """Check self_diagnostics daemon logs for startup time and errors"""
    log_checks = {
        "log_exists": False,
        "last_startup_time": None,
        "recent_exceptions": [],
        "log_lines": []
    }
    
    # Check common log locations
    log_paths = [
        "/tmp/self_diagnostics.log",
        "/var/log/self_diagnostics.log",
        "/home/workspace/self_diagnostics.log",
        "/tmp/self_diagnostics.err"
    ]
    
    found_log = None
    for path in log_paths:
        if os.path.exists(path):
            found_log = path
            break
    
    if found_log:
        log_checks["log_exists"] = True
        log_checks["log_path"] = found_log
        try:
            with open(found_log, 'r') as f:
                lines = f.readlines()
                log_checks["log_lines"] = len(lines)
                
                # Look for startup indicators
                for line in lines:
                    if "Starting" in line or "Started" in line or "Initializing" in line:
                        log_checks["last_startup_time"] = line.strip()
                    if "Exception" in line or "Error" in line or "Traceback" in line:
                        log_checks["recent_exceptions"].append(line.strip())
                
                # Get last 20 lines
                log_checks["last_lines"] = [l.strip() for l in lines[-20:]]
        except Exception as e:
            log_checks["read_error"] = str(e)
    else:
        log_checks["checked_paths"] = log_paths
    
    return log_checks

def check_scheduled_run_table():
    """Check scheduled_run table entries"""
    result = {"table_exists": False, "row_count": 0, "recent_runs": [], "error": None}
    
    try:
        # Check if table exists
        query_resp = write_service_query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_run'"
        )
        if query_resp.get("rows"):
            result["table_exists"] = True
            
            # Get row count
            count_resp = write_service_query("SELECT COUNT(*) as cnt FROM scheduled_run")
            if count_resp.get("rows"):
                result["row_count"] = count_resp["rows"][0].get("cnt", 0)
            
            # Get recent runs
            recent_resp = write_service_query(
                "SELECT * FROM scheduled_run ORDER BY run_at DESC LIMIT 10"
            )
            if recent_resp.get("rows"):
                result["recent_runs"] = recent_resp["rows"]
        else:
            result["error"] = "Table 'scheduled_run' does not exist"
    except Exception as e:
        result["error"] = str(e)
    
    return result

def check_service_health():
    """Check self_diagnostics health from service_health table"""
    result = {"record_exists": False, "last_heartbeat": None, "stale_minutes": None}
    
    try:
        resp = write_service_query(
            "SELECT * FROM service_health WHERE service='self_diagnostics'"
        )
        if resp.get("rows") and len(resp["rows"]) > 0:
            record = resp["rows"][0]
            result["record_exists"] = True
            result["last_heartbeat"] = record.get("last_heartbeat")
            
            # Calculate staleness
            if record.get("last_heartbeat"):
                hb_time = datetime.fromisoformat(record["last_heartbeat"])
                stale_seconds = (datetime.now() - hb_time).total_seconds()
                result["stale_minutes"] = round(stale_seconds / 60, 1)
                result["stale_seconds"] = round(stale_seconds, 0)
    except Exception as e:
        result["error"] = str(e)
    
    return result

def check_process_status():
    """Check if self_diagnostics process is running"""
    result = {"running": False, "pid": None, "uptime_seconds": None}
    
    try:
        # Check PID file
        pid_file = "/tmp/self_diagnostics.pid"
        if os.path.exists(pid_file):
            with open(pid_file, 'r') as f:
                pid = int(f.read().strip())
            result["pid"] = pid
            
            # Check if process is actually running
            try:
                os.kill(pid, 0)  # Signal 0 just checks if process exists
                result["running"] = True
            except OSError:
                result["running"] = False
                result["pid_dead"] = True
                
        # Also check via ps command
        ps_output = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True
        )
        for line in ps_output.stdout.split("\n"):
            if "self_diagnostics" in line and "python" in line:
                result["ps_line"] = line.strip()
                break
    except Exception as e:
        result["error"] = str(e)
    
    return result

def generate_diagnostic_report():
    """Generate full diagnostic report"""
    report = {
        "generated_at": datetime.now().isoformat(),
        "diagnostic_type": "self_diagnostics_stale",
        "threshold_minutes": 10,
        "findings": {}
    }
    
    print("=" * 60)
    print("SELF-DIAGNOSTICS STALE DIAGNOSTIC REPORT")
    print("=" * 60)
    print(f"Timestamp: {report['generated_at']}")
    print(f"Threshold: {report['threshold_minutes']} minutes")
    print()
    
    # 1. Daemon startup time from logs
    print("[1/4] Checking daemon logs...")
    log_info = check_daemon_logs()
    report["findings"]["daemon_logs"] = log_info
    if log_info.get("last_startup_time"):
        print(f"  Last startup: {log_info['last_startup_time']}")
    if log_info.get("recent_exceptions"):
        print(f"  Exceptions found: {len(log_info['recent_exceptions'])}")
        for exc in log_info["recent_exceptions"][:3]:
            print(f"    - {exc[:100]}")
    print()
    
    # 2. Any exceptions in recent output
    print("[2/4] Checking recent exceptions...")
    if log_info.get("recent_exceptions"):
        report["findings"]["exceptions"] = {
            "count": len(log_info["recent_exceptions"]),
            "exceptions": log_info["recent_exceptions"]
        }
        print(f"  Found {len(log_info['recent_exceptions'])} exceptions")
    else:
        print("  No exceptions found in logs")
        report["findings"]["exceptions"] = {"count": 0}
    print()
    
    # 3. Check scheduled_run table
    print("[3/4] Checking scheduled_run table...")
    scheduled_info = check_scheduled_run_table()
    report["findings"]["scheduled_run"] = scheduled_info
    print(f"  Table exists: {scheduled_info['table_exists']}")
    print(f"  Row count: {scheduled_info['row_count']}")
    if scheduled_info.get("recent_runs"):
        print(f"  Recent runs: {len(scheduled_info['recent_runs'])}")
    if scheduled_info.get("error"):
        print(f"  Error: {scheduled_info['error']}")
    print()
    
    # 4. write_service connectivity
    print("[4/4] Checking write_service connectivity...")
    conn_info = check_write_service_connectivity()
    report["findings"]["write_service"] = conn_info
    print(f"  Connected: {conn_info['connected']}")
    if conn_info.get("latency_ms"):
        print(f"  Latency: {conn_info['latency_ms']}ms")
    if conn_info.get("error"):
        print(f"  Error: {conn_info['error']}")
    print()
    
    # Bonus: Check process status and health
    print("[BONUS] Checking process status...")
    proc_info = check_process_status()
    report["findings"]["process_status"] = proc_info
    print(f"  Running: {proc_info['running']}")
    if proc_info.get("pid"):
        print(f"  PID: {proc_info['pid']}")
    print()
    
    print("[BONUS] Checking service_health table...")
    health_info = check_service_health()
    report["findings"]["service_health"] = health_info
    if health_info.get("stale_minutes") is not None:
        print(f"  Last heartbeat: {health_info['last_heartbeat']}")
        print(f"  Stale for: {health_info['stale_minutes']} minutes")
    print()
    
    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    stale_minutes = health_info.get("stale_minutes", 0)
    is_stale = stale_minutes > report["threshold_minutes"]
    
    print(f"Daemon Stale: {'YES' if is_stale else 'NO'} ({stale_minutes} min)")
    print(f"write_service OK: {'YES' if conn_info['connected'] else 'NO'}")
    print(f"Process Running: {'YES' if proc_info['running'] else 'NO'}")
    print(f"Exceptions Found: {len(log_info.get('recent_exceptions', []))}")
    
    report["summary"] = {
        "is_stale": is_stale,
        "stale_minutes": stale_minutes,
        "write_service_ok": conn_info["connected"],
        "process_running": proc_info["running"],
        "exception_count": len(log_info.get("recent_exceptions", []))
    }
    
    # Recommendations
    print()
    print("RECOMMENDATIONS:")
    if not conn_info["connected"]:
        print("  - write_service connectivity issue - check service")
    if not proc_info["running"]:
        print("  - Process not running - restart self_diagnostics daemon")
    if log_info.get("recent_exceptions"):
        print("  - Exceptions in logs - review stack traces above")
    if scheduled_info.get("row_count", 0) == 0:
        print("  - No scheduled_run entries - may need initialization")
    if is_stale:
        print("  - Daemon heartbeat stale - investigate hang or crash")
    
    return report

def main():
    report = generate_diagnostic_report()
    
    # Write report to file
    report_json = json.dumps(report, indent=2, default=str)
    
    with open(OUTPUT_FILE.replace('.py', '_report.json'), 'w') as f:
        f.write(report_json)
    
    print()
    print(f"Report written to: {OUTPUT_FILE.replace('.py', '_report.json')}")
    
    return report

if __name__ == "__main__":
    main()