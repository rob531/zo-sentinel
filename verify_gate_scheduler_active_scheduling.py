import requests
from datetime import datetime, timedelta

def verify_scheduling_activity() -> bool:
    """
    Verify if the `gate_scheduler` daemon is actively scheduling tasks by checking for recent entries
    in the `audit_log` or `mcp_signal_scores` tables that would be indicative of its work.

    Returns:
        bool: True if active scheduling is detected, False otherwise.
    """
    # Define the time threshold for recent activity (e.g., last 5 minutes)
    time_threshold = datetime.utcnow() - timedelta(minutes=5)

    # Query the audit_log table for recent gate_scheduler related entries
    audit_log_url = "http://write_service/audit_log"
    params = {
        "filter": {
            "timestamp": {"$gt": time_threshold.isoformat()},
            "action": {"$regex": "gate_scheduler"}
        },
        "limit": 1
    }
    response = requests.get(audit_log_url, params=params)
    if response.status_code == 200 and response.json():
        return True

    # Query the mcp_signal_scores table for recent updates
    signal_scores_url = "http://write_service/mcp_signal_scores"
    params = {
        "filter": {
            "last_updated": {"$gt": time_threshold.isoformat()}
        },
        "limit": 1
    }
    response = requests.get(signal_scores_url, params=params)
    if response.status_code == 200 and response.json():
        return True

    return False

if __name__ == "__main__":
    # Mock database simulation for testing
    class MockResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json_data = json_data

        def json(self):
            return self._json_data

    # Test case 1: Simulate recent activity in audit_log
    requests.get = lambda url, params: MockResponse(200, [{"timestamp": datetime.utcnow().isoformat(), "action": "gate_scheduler"}])
    assert verify_scheduling_activity() == True
    print("PASS")

    # Test case 2: Simulate no recent activity
    requests.get = lambda url, params: MockResponse(200, [])
    assert verify_scheduling_activity() == False
    print("PASS")