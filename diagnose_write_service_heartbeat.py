#!/usr/bin/env python3
"""
diagnose_write_service_heartbeat_stale.py
Diagnostic module to identify why write_service self-heartbeat is >3h stale.
"""

import sys
import time
import requests
from datetime import datetime, timedelta
import subprocess
import os

# Configuration
WRITE_SERVICE = "write_service"
STALE_THRESHOLD_HOURS = 3
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HEALTH_ENDPOINT = f"{WRITE_SERVICE_URL}/health"

# Known heartbeat daemon names
HEARTBEAT_DAEMONS = [
    "heartbeat_monitor",
    "write_service_heartbeat",
    "service_heartbeat_daemon",
    "sentinel_heartbeat",
    "zo_heartbeat",
    "heartbeat",
]


def query_service_health():
    """Query service_health table to get write_service heartbeat status."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": "SELECT service, last_heartbeat FROM service_health WHERE service = 'write_service'"},
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("rows", [])
    except Exception as e:
        print(f"[DIAG] Failed to query service_health: {e}")
    return []


def check_write_service_health_endpoint():
    """Check if write_service /health endpoint is responsive."""
    try:
        response = requests.get(HEALTH_ENDPOINT, timeout=5)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"[DIAG] write_service /health endpoint not responsive: {e}")
    return None


def check_running_heartbeat_processes():
    """Check for running heartbeat daemon processes."""
    running = []
    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            for daemon in HEARTBEAT_DAEMONS:
                if daemon in line.lower() and "grep" not in line:
                    running.append(line.strip())
    except Exception as e:
        print(f"[DIAG] Failed to check processes: {e}")
    return running


def check_heartbeat_pid_files():
    """Check for heartbeat PID files."""
    pid_files = {}
    for daemon in HEARTBEAT_DAEMONS:
        pid_path = f"/tmp/{daemon}.pid"
        if os.path.exists(pid_path):
            try:
                with open(pid_path, "r") as f:
                    pid = int(f.read().strip())
                    pid_files[daemon] = pid
            except Exception:
                pid_files[daemon] = "invalid"
    return pid_files


def analyze_heartbeat_staleness(last_heartbeat_str):
    """Analyze heartbeat staleness and compute age."""
    try:
        # Parse timestamp - assume ISO format or epoch
        if last_heartbeat_str:
            try:
                # Try ISO format
                hb_time = datetime.fromisoformat(last_heartbeat_str.replace("Z", "+00:00"))
            except Exception:
                # Try epoch
                hb_time = datetime.fromtimestamp(float(last_heartbeat_str))
            
            now = datetime.now(hb_time.tzinfo) if hb_time.tzinfo else datetime.now()
            age_seconds = (now - hb_time).total_seconds()
            age_hours = age_seconds / 3600
            return age_seconds, age_hours
    except Exception as e:
        print(f"[DIAG] Failed to parse heartbeat timestamp: {e}")
    return None, None


def main():
    print("=" * 60)
    print("DIAGNOSTIC: write_service heartbeat staleness")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 60)
    
    diagnosis = {
        "service_health_row_exists": False,
        "heartbeat_age_seconds": None,
        "is_stale": False,
        "endpoint_responsive": False,
        "heartbeat_daemons_running": [],
        "heartbeat_pid_files": {},
        "root_causes": [],
        "fix_directive": None
    }
    
    # Step 1: Check if write_service /health endpoint is responsive
    print("\n[STEP 1] Checking write_service /health endpoint...")
    endpoint_data = check_write_service_health_endpoint()
    if endpoint_data:
        diagnosis["endpoint_responsive"] = True
        print(f"  ✓ Endpoint responsive: {endpoint_data}")
    else:
        print("  ✗ Endpoint NOT responsive")
        diagnosis["root_causes"].append("write_service_endpoint_unresponsive")
        diagnosis["fix_directive"] = "INVESTIGATE: write_service process may be hung or crashed"
        print(f"\nDIAGNOSIS: write_service is not responding on {HEALTH_ENDPOINT}")
        print(f"FIX DIRECTIVE: {diagnosis['fix_directive']}")
        return
    
    # Step 2: Query service_health table
    print("\n[STEP 2] Querying service_health table...")
    rows = query_service_health()
    
    if rows:
        diagnosis["service_health_row_exists"] = True
        row = rows[0]
        last_heartbeat = row.get("last_heartbeat")
        print(f"  Found row: service={row.get('service')}, last_heartbeat={last_heartbeat}")
        
        age_seconds, age_hours = analyze_heartbeat_staleness(str(last_heartbeat))
        diagnosis["heartbeat_age_seconds"] = age_seconds
        
        if age_hours is not None:
            diagnosis["is_stale"] = age_hours > STALE_THRESHOLD_HOURS
            print(f"  Heartbeat age: {age_hours:.2f} hours")
            
            if diagnosis["is_stale"]:
                print(f"  ⚠ HEARTBEAT IS STALE (>{STALE_THRESHOLD_HOURS}h)")
            else:
                print(f"  ✓ Heartbeat is fresh")
    else:
        diagnosis["root_causes"].append("no_service_health_row")
        print("  ✗ No service_health row found for write_service")
        diagnosis["fix_directive"] = "CREATE: Insert write_service row into service_health table"
        print(f"\nDIAGNOSIS: write_service has no entry in service_health")
        print(f"FIX DIRECTIVE: {diagnosis['fix_directive']}")
        return
    
    # Step 3: Check for heartbeat daemon processes
    print("\n[STEP 3] Checking heartbeat daemon processes...")
    running_processes = check_running_heartbeat_processes()
    diagnosis["heartbeat_daemons_running"] = running_processes
    
    if running_processes:
        print(f"  Found {len(running_processes)} heartbeat-related processes:")
        for proc in running_processes[:5]:
            print(f"    - {proc[:100]}")
    else:
        print("  ✗ No heartbeat daemon processes found")
    
    # Step 4: Check PID files
    print("\n[STEP 4] Checking heartbeat PID files...")
    pid_files = check_heartbeat_pid_files()
    diagnosis["heartbeat_pid_files"] = pid_files
    
    if pid_files:
        print(f"  Found PID files:")
        for daemon, pid in pid_files.items():
            print(f"    - {daemon}.pid: {pid}")
    else:
        print("  ✗ No heartbeat PID files found")
    
    # Step 5: Determine root cause
    print("\n" + "=" * 60)
    print("DIAGNOSIS SUMMARY")
    print("=" * 60)
    
    if diagnosis["is_stale"]:
        # Identify root cause
        if not running_processes and not pid_files:
            diagnosis["root_causes"].append("heartbeat_daemon_not_running")
            print("ROOT CAUSE: No heartbeat daemon is running")
            print("  - No heartbeat processes found")
            print("  - No heartbeat PID files found")
            diagnosis["fix_directive"] = "LAUNCH: Start heartbeat_monitor daemon for write_service"
        elif running_processes:
            # Daemon running but heartbeat stale - potential race condition or bug
            diagnosis["root_causes"].append("heartbeat_daemon_running_but_stale")
            print("ROOT CAUSE: Heartbeat daemon may be stuck or misconfigured")
            print("  - Daemon appears running but not updating heartbeat")
            diagnosis["fix_directive"] = "RESTART: Restart heartbeat_monitor daemon (potential race condition)"
        else:
            diagnosis["root_causes"].append("unknown_race_condition")
            print("ROOT CAUSE: Unknown - possible race condition in heartbeat logic")
            diagnosis["fix_directive"] = "AUDIT: Review heartbeat daemon code for race conditions"
    else:
        print("Heartbeat is FRESH - no issue detected")
        diagnosis["fix_directive"] = "NONE: No fix needed"
    
    print("\nFIX DIRECTIVE:")
    print(f"  {diagnosis['fix_directive']}")
    
    # Output to logs
    log_path = f"/tmp/diagnose_heartbeat_{int(time.time())}.log"
    try:
        with open(log_path, "w") as f:
            f.write(f"Diagnostic Run: {datetime.now().isoformat()}\n")
            f.write(f"Heartbeat Age: {diagnosis.get('heartbeat_age_seconds', 'N/A')} seconds\n")
            f.write(f"Is Stale: {diagnosis.get('is_stale', 'N/A')}\n")
            f.write(f"Root Causes: {diagnosis.get('root_causes', [])}\n")
            f.write(f"Fix Directive: {diagnosis.get('fix_directive', 'N/A')}\n")
        print(f"\nDiagnosis logged to: {log_path}")
    except Exception as e:
        print(f"Failed to write log: {e}")
    
    return diagnosis


if __name__ == "__main__":
    main()