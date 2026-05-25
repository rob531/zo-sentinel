#!/usr/bin/env python3
"""
write_service heartbeat diagnostic restarter.
Checks write_service responsiveness and attempts to write fresh heartbeat.
DO NOT rebuild write_service - it's protected.
"""

import requests
import time
import sys
import os
from datetime import datetime

SERVICE_NAME = "write_service_heartbeat_restarter"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
POLL_SECS = 300
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

def check_single_instance():
    """Ensure only single instance running."""
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"[FATAL] Another instance running as PID {old_pid}")
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

def test_write_service_responsive():
    """Test write_service responsiveness via test write."""
    ts = datetime.now().isoformat()
    print(f"[{ts}] INFO: Testing write_service responsiveness...")
    
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/write", json={
            "table": "service_health",
            "rows": {
                "service": "__diagnostic_test__",
                "last_heartbeat": ts
            },
            "wait": True
        }, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            print(f"[{ts}] PASS: write_service responsive")
            print(f"[{ts}] Response: {data}")
            return True
        else:
            print(f"[{ts}] FAIL: write_service returned status {resp.status_code}")
            print(f"[{ts}] Response: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"[{ts}] FAIL: write_service unreachable: {e}")
        return False

def write_fresh_heartbeat():
    """Attempt to write fresh heartbeat for write_service."""
    ts = datetime.now().isoformat()
    print(f"[{ts}] INFO: Attempting to write fresh heartbeat for write_service...")
    
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/write", json={
            "table": "service_health",
            "rows": {
                "service": "write_service",
                "last_heartbeat": ts
            },
            "wait": True
        }, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            print(f"[{ts}] PASS: Fresh heartbeat written for write_service")
            print(f"[{ts}] Response: {data}")
            return True
        else:
            print(f"[{ts}] FAIL: Could not write heartbeat (status={resp.status_code})")
            print(f"[{ts}] Response: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"[{ts}] FAIL: Heartbeat write failed: {e}")
        return False

def get_heartbeat_status():
    """Query current heartbeat status for write_service."""
    ts = datetime.now().isoformat()
    print(f"[{ts}] INFO: Querying current write_service heartbeat status...")
    
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/query", json={
            "sql": "SELECT * FROM service_health WHERE service = 'write_service'"
        }, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            print(f"[{ts}] Current heartbeat record: {data}")
            return data
        else:
            print(f"[{ts}] FAIL: Could not query heartbeat (status={resp.status_code})")
            return None
    except Exception as e:
        print(f"[{ts}] FAIL: Heartbeat query failed: {e}")
        return None

def send_self_heartbeat():
    """Send heartbeat for this diagnostic service."""
    ts = datetime.now().isoformat()
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/write", json={
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": ts
            },
            "wait": True
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"[{ts}] Self-heartbeat failed: {e}")
        return False

def run_diagnostic_cycle():
    """Run one diagnostic cycle."""
    ts = datetime.now().isoformat()
    print(f"\n{'='*60}")
    print(f"write_service heartbeat diagnostic cycle - {ts}")
    print(f"{'='*60}")
    
    results = {
        'ts': ts,
        'is_responsive': False,
        'heartbeat_updated': False
    }
    
    # Step 1: Query current heartbeat status
    print("\n[1] Querying current heartbeat status...")
    current_status = get_heartbeat_status()
    results['current_status'] = current_status
    
    # Step 2: Test write_service responsiveness
    print("\n[2] Testing write_service responsiveness...")
    results['is_responsive'] = test_write_service_responsive()
    
    # Step 3: Attempt to write fresh heartbeat
    if results['is_responsive']:
        print("\n[3] Attempting to write fresh heartbeat...")
        results['heartbeat_updated'] = write_fresh_heartbeat()
        
        # Step 4: Verify heartbeat was written
        if results['heartbeat_updated']:
            print("\n[4] Verifying heartbeat update...")
            verify_status = get_heartbeat_status()
            results['verify_status'] = verify_status
    else:
        print("\n[3] SKIPPED: write_service not responsive")
        print("\n[4] SKIPPED: write_service not responsive")
    
    # Summary
    print(f"\n{'='*60}")
    print("DIAGNOSTIC SUMMARY")
    print(f"{'='*60}")
    print(f"  Timestamp: {ts}")
    print(f"  write_service responsive: {results['is_responsive']}")
    print(f"  Heartbeat updated: {results['heartbeat_updated']}")
    if current_status:
        print(f"  Previous heartbeat: {current_status.get('rows', [{}])[0].get('last_heartbeat', 'N/A') if current_status.get('rows') else 'N/A'}")
    print(f"{'='*60}\n")
    
    return results

def run():
    """Main daemon loop."""
    print(f"Starting {SERVICE_NAME}...")
    check_single_instance()
    start_time = time.time()
    
    print(f"Entering monitoring loop (poll_interval={POLL_SECS}s)...")
    print("Per Appendix A: Diagnostic only - do NOT propose rebuild")
    
    while True:
        ts = datetime.now().isoformat()
        uptime = int(time.time() - start_time)
        
        # Run diagnostic cycle
        results = run_diagnostic_cycle()
        
        # Send self heartbeat
        heartbeat_ok = send_self_heartbeat()
        
        print(f"[{ts}] CYCLE_COMPLETE: uptime={uptime}s, "
              f"write_service_responsive={results['is_responsive']}, "
              f"heartbeat_updated={results['heartbeat_updated']}, "
              f"self_heartbeat={'ok' if heartbeat_ok else 'FAIL'}")
        
        time.sleep(POLL_SECS)

if __name__ == '__main__':
    run()