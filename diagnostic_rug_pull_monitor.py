import requests
from datetime import datetime, timedelta
import logging
from typing import Dict, List

logging.basicConfig(level=logging.INFO)

def get_last_heartbeat(service_health: str) -> str:
    response = requests.get(f"http://127.0.0.1:8772/{service_health}")
    data = response.json()
    if "rows" in data and len(data["rows"]) > 0:
        return data["rows"][list(data["rows"].keys())[0]]["last_heartbeat"]
    else:
        return None

def check_pid_file(pid_file_path: str) -> bool:
    try:
        with open(pid_file_path, 'r') as f:
            pass
        return True
    except FileNotFoundError:
        return False

def read_stderr(stderr_path: str) -> List[str]:
    try:
        with open(stderr_path, 'r') as f:
            return [line.strip() for line in f.readlines()]
    except FileNotFoundError:
        return []

def diagnose_stale_heartbeat(service_name: str) -> Dict:
    service_health = "service_health"
    last_heartbeat_timestamp = get_last_heartbeat(service_health)
    if last_heartbeat_timestamp is None or datetime.strptime(last_heartbeat_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ") < (datetime.now() - timedelta(seconds=28800)):
        pid_file_path = "/var/run/rug_pull_monitor.pid"
        if not check_pid_file(pid_file_path):
            stderr_path = "/var/log/rug_pull_monitor.stderr"
            log_output = read_stderr(stderr_path)
            return {
                "service_name": service_name,
                "last_heartbeat_timestamp": last_heartbeat_timestamp,
                "pid_file_exists": False,
                "log_output": log_output
            }
    return {
        "service_name": service_name,
        "last_heartbeat_timestamp": last_heartbeat_timestamp,
        "pid_file_exists": True,
        "log_output": []
    }

def run():
    logging.info("Starting diagnostic module")
    
    service_health = "service_health"
    data = {
        "table": "diagnostic_rug_pull_monitor_stale",
        "rows": [{"last_heartbeat_timestamp": None, "pid_file_exists": False, "log_output": []}],
        "wait": True
    }
    requests.post(f"http://127.0.0.1:8772/write", json=data)
    
    service_name = "rug_pull_monitor"
    diagnosis = diagnose_stale_heartbeat(service_name)
    data = {
        "table": "diagnostic_rug_pull_monitor_stale",
        "rows": [diagnosis],
        "wait": True
    }
    requests.post(f"http://127.0.0.1:8772/write", json=data)

def cycle():
    run()