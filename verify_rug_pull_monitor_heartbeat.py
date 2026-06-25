import requests
import time
from datetime import datetime, timedelta

def verify_rug_pull_monitor_heartbeat():
    # Query the service_health table for rug_pull_monitor's last heartbeat and status
    query = """
    SELECT last_heartbeat, status
    FROM service_health
    WHERE service_name = 'rug_pull_monitor'
    """
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query}
    )
    response.raise_for_status()
    data = response.json()

    if not data['results']:
        print("FAIL: No heartbeat record found for rug_pull_monitor")
        return False

    last_heartbeat = data['results'][0]['last_heartbeat']
    status = data['results'][0]['status']

    # Convert last_heartbeat to datetime object
    last_heartbeat_time = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))

    # Calculate the time difference
    time_difference = datetime.utcnow() - last_heartbeat_time

    # Check if the last heartbeat is within the threshold (5 minutes)
    threshold = timedelta(minutes=5)
    if time_difference <= threshold and status == 'healthy':
        print("PASS: rug_pull_monitor heartbeat is recent and status is healthy")
        return True
    else:
        print(f"FAIL: rug_pull_monitor heartbeat is not recent or status is not healthy. Last heartbeat: {last_heartbeat}, Status: {status}")
        return False

if __name__ == "__main__":
    result = verify_rug_pull_monitor_heartbeat()
    assert result, "rug_pull_monitor heartbeat verification failed"