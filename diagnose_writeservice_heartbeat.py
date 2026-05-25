#!/usr/bin/env python3
"""
diagnose_writeservice_heartbeat.py
Diagnostic module to investigate stale heartbeat in write_service.
"""

import requests
import time
from datetime import datetime

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
WRITE_SERVICE_NAME = "write_service"

def query_service_health():
    """Query service_health table for write_service last_heartbeat."""
    payload = {
        "sql": "SELECT service, last_heartbeat FROM service_health WHERE service = 'write_service'"
    }
    try:
        response = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e)}

def test_write_service_write():
    """Test that write_service write endpoint still works."""
    test_timestamp = datetime.now().isoformat()
    payload = {
        "table": "service_health",
        "rows": {"service": "diagnostic_probe", "last_heartbeat": test_timestamp},
        "wait": True
    }
    try:
        response = requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=10)
        response.raise_for_status()
        return {"success": True, "timestamp": test_timestamp}
    except Exception as e:
        return {"success": False, "error": str(e)}

def test_write_service_query():
    """Test that write_service query endpoint still works."""
    payload = {
        "sql": "SELECT 1 AS test"
    }
    try:
        response = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=10)
        response.raise_for_status()
        return {"success": True, "response": response.json()}
    except Exception as e:
        return {"success": False, "error": str(e)}

def check_heartbeat_age(last_heartbeat_str):
    """Calculate how stale the heartbeat is."""
    if not last_heartbeat_str:
        return None, None
    try:
        last_dt = datetime.fromisoformat(last_heartbeat_str.replace('Z', '+00:00'))
        now = datetime.now()
        age_seconds = (now - last_dt).total_seconds()
        hours = int(age_seconds // 3600)
        minutes = int((age_seconds % 3600) // 60)
        return age_seconds, f"{hours}h{minutes}m"
    except Exception:
        return None, None

def check_pid_exists():
    """Check if write_service PID file exists."""
    import os
    pid_file = "/tmp/write_service.pid"
    if os.path.exists(pid_file):
        try:
            with open(pid_file, 'r') as f:
                pid = f.read().strip()
            return True, pid
        except:
            return True, "unknown"
    return False, None

def check_process_running(pid):
    """Check if process with given PID is running."""
    import os
    import signal
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ProcessLookupError, ValueError):
        return False

def main():
    print("=" * 60)
    print("DIAGNOSTIC: write_service heartbeat analysis")
    print("=" * 60)
    print(f"\nTimestamp: {datetime.now().isoformat()}")
    
    print("\n[1] Checking PID file...")
    pid_exists, pid = check_pid_exists()
    if pid_exists:
        print(f"  PID file exists: {pid}")
        if check_process_running(pid):
            print(f"  Process {pid} is RUNNING")
        else:
            print(f"  Process {pid} is NOT running (stale PID file)")
    else:
        print("  No PID file found")
    
    print("\n[2] Querying service_health for write_service heartbeat...")
    health_data = query_service_health()
    
    if "error" in health_data:
        print(f"  ERROR: Cannot query service_health - {health_data['error']}")
        return {"diagnosis": "unreachable", "fix": "restart write_service"}
    
    rows = health_data.get("rows", [])
    if not rows:
        print("  WARNING: write_service not found in service_health table")
        print("  Service may never have registered a heartbeat")
        return {"diagnosis": "no_heartbeat_record", "fix": "restart write_service"}
    else:
        last_heartbeat = rows[0].get("last_heartbeat")
        age_seconds, age_str = check_heartbeat_age(last_heartbeat)
        print(f"  Last heartbeat: {last_heartbeat}")
        print(f"  Age: {age_str} ({age_seconds:.0f}s)")
        
        if age_seconds and age_seconds > 600:
            print("  STATUS: HEARTBEAT IS STALE (>10 minutes)")
        elif age_seconds and age_seconds > 300:
            print("  STATUS: HEARTBEAT IS OLD (>5 minutes)")
        elif age_seconds:
            print("  STATUS: HEARTBEAT IS FRESH")
    
    print("\n[3] Testing write_service query endpoint...")
    query_test = test_write_service_query()
    if query_test["success"]:
        print("  ✓ Query endpoint: WORKING")
    else:
        print(f"  ✗ Query endpoint: FAILED - {query_test['error']}")
    
    print("\n[4] Testing write_service write endpoint...")
    write_test = test_write_service_write()
    if write_test["success"]:
        print("  ✓ Write endpoint: WORKING")
    else:
        print(f"  ✗ Write endpoint: FAILED - {write_test['error']}")
    
    print("\n[5] Checking if write_service has self-heartbeat mechanism...")
    print("  Looking for heartbeat pattern in codebase...")
    import os
    import glob
    
    heartbeat_patterns = []
    for pattern in ['/home/workspace/zo_sentinel/**/*write*.py', '/home/workspace/zo_sentinel/**/write_service*.py']:
        for f in glob.glob(pattern, recursive=True):
            try:
                with open(f, 'r') as file:
                    content = file.read()
                    if 'heartbeat' in content.lower() or 'service_health' in content:
                        heartbeat_patterns.append(f)
            except:
                pass
    
    if heartbeat_patterns:
        print(f"  Found potential heartbeat logic in: {heartbeat_patterns}")
    else:
        print("  No obvious self-heartbeat mechanism found in code")
    
    print("\n" + "=" * 60)
    print("DIAGNOSIS SUMMARY")
    print("=" * 60)
    
    if write_test["success"] and query_test["success"]:
        print("\n✓ write_service endpoints are RESPONSIVE")
        if age_seconds and age_seconds > 300:
            print("✗ BUT heartbeat is STALE")
            print("\nLIKELY CAUSE: write_service is running but its internal")
            print("              heartbeat/scheduler has stopped or is blocked")
            print("\nSUGGESTED FIX:")
            print("  Option A: Restart write_service (cleanest)")
            print(f"    pkill -f write_service && nohup python3 -m write_service &")
            print("  Option B: If using supervisord/systemd, check the service logs")
            print("  Option C: Check for blocking operations in the heartbeat loop")
        else:
            print("✓ Heartbeat appears to be updating normally")
            print("  No action required")
    else:
        print("\n✗ write_service endpoints are UNRESPONSIVE")
        print("\nLIKELY CAUSE: write_service process has crashed or is hung")
        print("\nSUGGESTED FIX:")
        print("  Force kill and restart:")
        print("    pkill -9 -f write_service && sleep 2 && cd /home/workspace/zo_sentinel && python3 write_service.py &")
        print("  Or use the restart mechanism if available")

if __name__ == '__main__':
    result = main()
    if result:
        print(f"\nReturn result: {result}")