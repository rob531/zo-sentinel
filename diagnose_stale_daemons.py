#!/usr/bin/env python3
"""Stale Daemon Diagnostic Script for ZO-SENTINEL."""
import time
import subprocess
import requests
from datetime import datetime

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HEALTH_TIMEOUT = 3

DAEMONS = [
    {"name": "write_service", "process_pattern": "write_service", "health_port": 8772, "health_path": "/health"},
    {"name": "mcp_scanner", "process_pattern": "mcp_scanner", "health_port": None, "health_path": None},
    {"name": "rug_pull_monitor", "process_pattern": "rug_pull_monitor", "health_port": None, "health_path": None},
    {"name": "anti_entropy", "process_pattern": "anti_entropy", "health_port": None, "health_path": None},
    {"name": "wisdom_synthesiser", "process_pattern": "wisdom_synthesiser", "health_port": None, "health_path": None},
    {"name": "self_diagnostics", "process_pattern": "self_diagnostics", "health_port": None, "health_path": None},
]

def query_service_health():
    """Query service_health table via write_service HTTP API."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": "SELECT service, last_heartbeat FROM service_health"},
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        return {row["service"]: row["last_heartbeat"] for row in data.get("rows", [])}
    except Exception as e:
        print(f"[ERROR] Failed to query service_health: {e}")
        return {}

def check_process_running(process_pattern):
    """Check if process is running via ps."""
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5
        )
        for line in result.stdout.split("\n"):
            if process_pattern in line and "grep" not in line and "diagnose" not in line:
                return True
        return False
    except Exception as e:
        print(f"[ERROR] Failed to ps aux: {e}")
        return False

def check_health_endpoint(port, path):
    """Attempt lightweight health check via HTTP."""
    if not port or not path:
        return None
    try:
        resp = requests.get(
            f"http://127.0.0.1:{port}{path}",
            timeout=HEALTH_TIMEOUT
        )
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False

def parse_heartbeat(heartbeat_str):
    """Parse heartbeat timestamp for display."""
    if not heartbeat_str:
        return "NO HEARTBEAT RECORD"
    return heartbeat_str

def diagnose_daemon(daemon_info, heartbeat):
    """Diagnose a single daemon."""
    name = daemon_info["name"]
    process_pattern = daemon_info["process_pattern"]
    health_port = daemon_info["health_port"]
    health_path = daemon_info["health_path"]

    print(f"\n{'='*60}")
    print(f"DAEMON: {name}")
    print(f"{'='*60}")

    heartbeat_display = parse_heartbeat(heartbeat)
    print(f"  Heartbeat Record: {heartbeat_display}")

    is_running = check_process_running(process_pattern)
    print(f"  Process Running:  {is_running}")

    if not is_running:
        return {"daemon": name, "status": "dead", "reason": "Process not found in ps aux"}

    if health_port and health_path:
        health_ok = check_health_endpoint(health_port, health_path)
        print(f"  Health Endpoint:  http://127.0.0.1:{health_port}{health_path} -> {health_ok}")

        if heartbeat == "NO HEARTBEAT RECORD" or not heartbeat:
            if health_ok:
                return {"daemon": name, "status": "healthy_but_stale", "reason": "Process running and responsive but no heartbeat recorded"}
            else:
                return {"daemon": name, "status": "unresponsive", "reason": "Process running but health endpoint not responding"}
        elif not health_ok:
            return {"daemon": name, "status": "unresponsive", "reason": "Process running but health endpoint not responding"}
        else:
            return {"daemon": name, "status": "healthy", "reason": "Process running, heartbeat active, health endpoint responding"}
    else:
        if heartbeat == "NO HEARTBEAT RECORD" or not heartbeat:
            return {"daemon": name, "status": "healthy_but_stale", "reason": "Process running but no heartbeat recorded (no HTTP health endpoint)"}
        else:
            return {"daemon": name, "status": "healthy", "reason": "Process running with heartbeat recorded (no HTTP health endpoint)"}

def main():
    """Run stale daemon diagnostics."""
    print("ZO-SENTINEL Stale Daemon Diagnostic")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("-" * 60)

    heartbeats = query_service_health()
    print(f"\nQueried service_health table: {len(heartbeats)} records retrieved")

    results = []
    for daemon in DAEMONS:
        heartbeat = heartbeats.get(daemon["name"], None)
        diagnosis = diagnose_daemon(daemon, heartbeat)
        results.append(diagnosis)

    print(f"\n\n{'='*60}")
    print("DIAGNOSTIC SUMMARY")
    print(f"{'='*60}")

    counts = {"healthy": 0, "healthy_but_stale": 0, "dead": 0, "unresponsive": 0}
    for r in results:
        status = r["status"]
        counts[status] = counts.get(status, 0) + 1
        print(f"\n  {r['daemon']}:")
        print(f"    Status: {status.upper()}")
        print(f"    Reason: {r['reason']}")

    print(f"\n{'='*60}")
    print("TOTALS")
    print(f"{'='*60}")
    print(f"  Healthy:          {counts.get('healthy', 0)}")
    print(f"  Healthy But Stale:{counts.get('healthy_but_stale', 0)}")
    print(f"  Dead:             {counts.get('dead', 0)}")
    print(f"  Unresponsive:      {counts.get('unresponsive', 0)}")
    print(f"\nNOTE: This is DIAGNOSTIC ONLY. No restarts performed.")

    return results

if __name__ == "__main__":
    main()