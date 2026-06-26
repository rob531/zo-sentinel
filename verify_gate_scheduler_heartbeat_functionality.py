import requests
import time
from datetime import datetime, timedelta

def check_heartbeat() -> bool:
    """Check if gate_scheduler has sent a heartbeat in the last 10 minutes."""
    try:
        # Calculate the threshold time (10 minutes ago)
        threshold_time = datetime.utcnow() - timedelta(minutes=10)

        # Query the service_health table for gate_scheduler heartbeats
        query = f"""
        SELECT last_heartbeat
        FROM service_health
        WHERE service = 'gate_scheduler'
        AND last_heartbeat >= '{threshold_time.isoformat()}'
        """
        response = requests.post(
            "http://127.0.0.1:8772/write_service",
            json={"query": query}
        )
        response.raise_for_status()

        # Check if any results were returned
        data = response.json()
        return len(data.get("results", [])) > 0

    except requests.RequestException as e:
        print(f"Error querying service_health table: {e}")
        return False

if __name__ == "__main__":
    if check_heartbeat():
        print("PASS: Gate Scheduler heartbeat is functional.")
    else:
        print("FAIL: Gate Scheduler heartbeat is not functional.")
        exit(1)