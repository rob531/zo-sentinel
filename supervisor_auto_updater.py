#!/usr/bin/env python3
"""
ZO-SENTINEL: Supervisor Auto-Updater Daemon
Checks service health and restarts unhealthy services.
"""
import os
import time
import signal
import subprocess
from datetime import datetime
import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
SERVICE_NAME = "supervisor_auto_updater"
HEARTBEAT_INTERVAL = 30
HEALTH_CHECK_INTERVAL = 60
STALE_THRESHOLD_SECONDS = 180

PID_DIR = "/tmp"
SERVICE_SCRIPTS = {
    "email_guid_auth": "/home/workspace/zo_sentinel/email_guid_auth.py",
    "advanced_filter_api": "/home/workspace/zo_sentinel/advanced_filter_api.py",
    "forensic_detail_api": "/home/workspace/zo_sentinel/forensic_detail_api.py",
    "manual_override_api": "/home/workspace/zo_sentinel/manual_override_api.py",
}

running = True

def check_single_instance():
    """Ensure only one instance of this daemon runs."""
    pid_file = f"{PID_DIR}/{SERVICE_NAME}.pid"
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"Another instance (PID {old_pid}) is running. Exiting.")
            exit(1)
        except OSError:
            pass
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

def cleanup_handler(signum, frame):
    global running
    running = False
    pid_file = f"{PID_DIR}/{SERVICE_NAME}.pid"
    if os.path.exists(pid_file):
        os.remove(pid_file)
    exit(0)

def send_heartbeat():
    """Send service heartbeat to write_service."""
    try:
        requests.post(f"{WRITE_SERVICE}/write", json={
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.utcnow().isoformat()
            },
            "wait": True
        }, timeout=5)
    except Exception:
        pass

def get_unhealthy_services():
    """Query write_service for stale services."""
    try:
        resp = requests.post(f"{WRITE_SERVICE}/query", json={
            "sql": "SELECT service, last_heartbeat FROM service_health"
        }, timeout=10)
        data = resp.json()
        unhealthy = []
        
        for row in data.get("rows", []):
            last_hb = datetime.fromisoformat(row["last_heartbeat"])
            age = (datetime.utcnow() - last_hb).total_seconds()
            if age > STALE_THRESHOLD_SECONDS:
                unhealthy.append(row["service"])
        
        return unhealthy
    except Exception:
        return []

def restart_service(service_name: str):
    """Restart an unhealthy service."""
    if service_name not in SERVICE_SCRIPTS:
        return False
    
    script = SERVICE_SCRIPTS[service_name]
    if not os.path.exists(script):
        return False
    
    try:
        subprocess.Popen(["python3", script])
        return True
    except Exception:
        return False

def run():
    check_single_instance()
    signal.signal(signal.SIGINT, cleanup_handler)
    signal.signal(signal.SIGTERM, cleanup_handler)
    
    print(f"{SERVICE_NAME} started (PID {os.getpid()})")
    
    last_health_check = 0
    
    while running:
        now = time.time()
        
        send_heartbeat()
        
        if now - last_health_check >= HEALTH_CHECK_INTERVAL:
            unhealthy = get_unhealthy_services()
            for svc in unhealthy:
                print(f"Restarting unhealthy service: {svc}")
                if restart_service(svc):
                    print(f"  -> {svc} restarted successfully")
                else:
                    print(f"  -> Failed to restart {svc}")
            last_health_check = now
        
        time.sleep(HEARTBEAT_INTERVAL)

if __name__ == "__main__":
    run()
