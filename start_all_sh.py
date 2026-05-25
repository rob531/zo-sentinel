#!/usr/bin/env python3
import subprocess
import time
import os
import signal
import requests
import json
from datetime import datetime
from fastapi import FastAPI
import uvicorn

app = FastAPI()
SERVICE_PORT = 8788
HEARTBEAT_INTERVAL = 30
PID_FILE = "/tmp/start_all_sh.pid"

SERVICES = [
    {"name": "write_service", "port": 8772, "module": "write_service"},
    {"name": "inference_router", "port": 8773, "module": "inference_router", "wait_for": 8772},
    {"name": "email_guid_auth", "port": 8775, "module": "email_guid_auth", "wait_for": 8772},
    {"name": "manual_override_api", "port": 8776, "module": "manual_override_api", "wait_for": 8772},
    {"name": "advanced_filter_api", "port": 8777, "module": "advanced_filter_api", "wait_for": 8772},
    {"name": "forensic_detail_api", "port": 8779, "module": "forensic_detail_api", "wait_for": 8772},
    {"name": "approval_workflow", "port": 8780, "module": "approval_workflow", "wait_for": 8772},
    {"name": "registry_api", "port": 8781, "module": "registry_api", "wait_for": 8772},
    {"name": "search_api", "port": 8782, "module": "search_api", "wait_for": 8772},
    {"name": "nl_query_engine", "port": 8784, "module": "nl_query_engine", "wait_for": 8772},
    {"name": "rule_engine_api", "port": 8785, "module": "rule_engine_api", "wait_for": 8772},
    {"name": "pi_flagged_review", "port": 8792, "module": "pi_flagged_review", "wait_for": 8772},
    {"name": "ui_server", "port": 8790, "module": "ui_server", "wait_for": 8772},
]

start_time = datetime.now()
running_processes = {}


def check_single_instance():
    if os.path.exists(PID_FILE):
        with open(PID_FILE, "r") as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            print(f"Service already running with PID {old_pid}")
            return False
        except (OSError, ValueError):
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def get_uptime_seconds():
    delta = datetime.now() - start_time
    return int(delta.total_seconds())


def send_heartbeat():
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": "start_all_sh",
                "last_heartbeat": datetime.now().isoformat()
            },
            "wait": True
        }
        requests.post("http://127.0.0.1:8772/write", json=payload, timeout=5)
    except Exception as e:
        print(f"Heartbeat failed: {e}")


def wait_for_service(port, timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
            if resp.status_code == 200:
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)
    return False


def start_service(service_info):
    name = service_info["name"]
    module = service_info["module"]
    port = service_info["port"]
    wait_for = service_info.get("wait_for")
    
    if wait_for:
        print(f"Waiting for dependency on port {wait_for} for {name}...")
        if not wait_for_service(wait_for):
            print(f"Warning: dependency {wait_for} not available for {name}")
    
    print(f"Starting {name} on port {port}...")
    try:
        proc = subprocess.Popen(
            ["python3", "-m", module],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        running_processes[name] = proc
        time.sleep(2)
        return True
    except Exception as e:
        print(f"Failed to start {name}: {e}")
        return False


def stop_all_services():
    for name, proc in running_processes.items():
        print(f"Stopping {name}...")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    running_processes.clear()


def signal_handler(signum, frame):
    print("Received shutdown signal...")
    stop_all_services()
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    exit(0)


def run():
    if not check_single_instance():
        return
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 50)
    print("ZO-SENTINEL Service Orchestrator")
    print("=" * 50)
    
    for service in SERVICES:
        start_service(service)
    
    print("\nAll services started. Entering monitoring loop...")
    print(f"Heartbeat interval: {HEARTBEAT_INTERVAL} seconds")
    print("=" * 50)
    
    send_heartbeat()
    
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        send_heartbeat()
        
        for name, proc in list(running_processes.items()):
            if proc.poll() is not None:
                print(f"Warning: {name} has stopped unexpectedly. Restarting...")
                service_info = next(s for s in SERVICES if s["name"] == name)
                start_service(service_info)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "start_all_sh",
        "uptime": get_uptime_seconds()
    }


@app.get("/services")
def list_services():
    status = {}
    for name, proc in running_processes.items():
        status[name] = "running" if proc.poll() is None else "stopped"
    return {"services": status}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORT)