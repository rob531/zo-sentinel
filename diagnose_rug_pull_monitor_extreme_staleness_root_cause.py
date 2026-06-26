import requests
import time
from datetime import datetime, timedelta

def get_last_heartbeat():
    url = "http://write_service/query"
    params = {
        "table": "service_health",
        "service": "rug_pull_monitor"
    }
    response = requests.get(url, params=params)
    if response.status_code == 200:
        data = response.json()
        if data:
            return data[0].get("last_heartbeat")
    return None

def diagnose_staleness():
    last_heartbeat = get_last_heartbeat()
    if last_heartbeat is None:
        print("Error: Could not retrieve last heartbeat from service_health table.")
        return

    last_heartbeat_time = datetime.strptime(last_heartbeat, "%Y-%m-%d %H:%M:%S")
    current_time = datetime.now()
    time_diff = current_time - last_heartbeat_time

    if time_diff > timedelta(minutes=10):
        print(f"Diagnostic: The rug_pull_monitor daemon is extremely stale. Last heartbeat was at {last_heartbeat_time}.")
        print("Potential causes for prolonged inactivity:")
        print("- Configuration issues")
        print("- Dependency failures")
        print("- Process failures")
    else:
        print("Diagnostic: The rug_pull_monitor daemon is active. Last heartbeat was at", last_heartbeat_time)

if __name__ == '__main__':
    # Simulate a stale heartbeat check
    print("Running self-test...")
    test_heartbeat_time = (datetime.now() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")

    # Mock the get_last_heartbeat function for testing
    original_get_last_heartbeat = get_last_heartbeat
    def mock_get_last_heartbeat():
        return test_heartbeat_time
    globals()['get_last_heartbeat'] = mock_get_last_heartbeat

    diagnose_staleness()

    # Restore the original function
    globals()['get_last_heartbeat'] = original_get_last_heartbeat

    # Assert that the diagnostic correctly identifies the staleness
    assert "extremely stale" in open("diagnostic_output.txt", "r").read(), "Self-test failed: Diagnostic did not correctly identify staleness."
    print("PASS")