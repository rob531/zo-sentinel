import requests
import json
import time
from datetime import datetime, timedelta

# Configuration
GATE_SCHEDULER_API_URL = "http://localhost:8000/api/gate_scheduler"
WRITE_SERVICE_API_URL = "http://localhost:8000/api/write_service"
AUDIT_LOG_QUERY_URL = "http://localhost:8000/api/query/audit_log"
MCP_SUBMISSIONS_QUERY_URL = "http://localhost:8000/api/query/mcp_submissions"

def verify_gate_scheduler_heartbeat():
    """Check if the gate_scheduler is alive by verifying its heartbeat."""
    try:
        response = requests.get(f"{GATE_SCHEDULER_API_URL}/heartbeat")
        response.raise_for_status()
        return response.json().get("status") == "alive"
    except requests.RequestException as e:
        print(f"Error checking gate_scheduler heartbeat: {e}")
        return False

def simulate_gate_scheduling_request():
    """Simulate a gate scheduling request via the write_service."""
    try:
        test_data = {
            "gate_id": "test_gate",
            "schedule_time": datetime.utcnow().isoformat(),
            "action": "open"
        }
        response = requests.post(f"{WRITE_SERVICE_API_URL}/schedule_gate", json=test_data)
        response.raise_for_status()
        return response.json().get("status") == "scheduled"
    except requests.RequestException as e:
        print(f"Error simulating gate scheduling request: {e}")
        return False

def check_recent_scheduling_activity():
    """Check for recent scheduling activity in the audit_log or mcp_submissions tables."""
    try:
        # Query audit_log for recent scheduling events
        audit_log_params = {
            "table": "audit_log",
            "where": f"event_type = 'gate_scheduled' AND timestamp > '{datetime.utcnow() - timedelta(minutes=5)}'"
        }
        audit_response = requests.post(AUDIT_LOG_QUERY_URL, json=audit_log_params)
        audit_response.raise_for_status()
        audit_log_entries = audit_response.json().get("data", [])

        # Query mcp_submissions for recent submissions triggered by gate_scheduler
        mcp_params = {
            "table": "mcp_submissions",
            "where": f"triggered_by = 'gate_scheduler' AND submission_time > '{datetime.utcnow() - timedelta(minutes=5)}'"
        }
        mcp_response = requests.post(MCP_SUBMISSIONS_QUERY_URL, json=mcp_params)
        mcp_response.raise_for_status()
        mcp_submissions = mcp_response.json().get("data", [])

        return len(audit_log_entries) > 0 or len(mcp_submissions) > 0
    except requests.RequestException as e:
        print(f"Error checking recent scheduling activity: {e}")
        return False

def main():
    # Verify gate_scheduler is alive
    if not verify_gate_scheduler_heartbeat():
        print("FAIL: gate_scheduler is not alive")
        return

    # Simulate a gate scheduling request
    if not simulate_gate_scheduling_request():
        print("FAIL: Could not simulate gate scheduling request")
        return

    # Wait a short time for the scheduler to process the request
    time.sleep(5)

    # Check for recent scheduling activity
    if not check_recent_scheduling_activity():
        print("FAIL: No recent scheduling activity found")
        return

    print("PASS: gate_scheduler is alive and performing its core function of scheduling")

if __name__ == "__main__":
    main()