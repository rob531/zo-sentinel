import requests
import json
import subprocess
from datetime import datetime

# Configuration
SERVICE_URL = "http://localhost:8772/service_health"
LOG_PATH = "/var/log/zo-sentinel/wisdom_synthesiser.log"
DAEMON_NAME = "wisdom_synthesiser"

def diagnose():
    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "target": DAEMON_NAME,
        "status": "UNKNOWN",
        "findings": []
    }

    # 1. Query Service Health
    try:
        response = requests.get(SERVICE_URL, timeout=5)
        data = response.json()
        report["health_data"] = data
        
        last_hb = data.get("last_heartbeat")
        status = data.get("status")
        
        if status != "RUNNING":
            report["status"] = "STUCK_OR_STOPPED"
            report["findings"].append(f"Service status reported as {status}")
        elif not last_hb:
            report["status"] = "HEARTBEAT_MISCONFIGURED"
            report["findings"].append("Heartbeat column missing or null in service_health")
    except Exception as e:
        report["status"] = "DEAD"
        report["findings"].append(f"Failed to reach service_health: {str(e)}")

    # 2. Review Logs
    try:
        log_tail = subprocess.check_output(["tail", "-n", "50", LOG_PATH], text=True)
        if "ERROR" in log_tail or "CRITICAL" in log_tail:
            report["findings"].append("Errors detected in recent logs.")
        report["log_snippet"] = log_tail[-500:]
    except Exception as e:
        report["findings"].append(f"Could not read logs: {str(e)}")

    # 3. Final Diagnosis
    if report["status"] == "DEAD":
        report["diagnosis"] = "Daemon process is unresponsive or crashed. Check systemd/supervisor status."
    elif "Errors detected" in str(report["findings"]):
        report["diagnosis"] = "Daemon is stuck due to internal runtime exception. See log_snippet."
    else:
        report["diagnosis"] = "Daemon appears active but heartbeat is stale. Likely misconfiguration or clock drift."

    print(json.dumps(report, indent=4))

if __name__ == "__main__":
    diagnose()