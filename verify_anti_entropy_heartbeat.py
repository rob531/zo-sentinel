import requests
import sys
from datetime import datetime, timedelta, timezone

# Configuration
QUERY_ENDPOINT = "http://localhost:8080/write_service/query"
SERVICE_NAME = "anti_entropy"
FRESHNESS_THRESHOLD_MINUTES = 60

def verify_heartbeat():
    query = f"SELECT last_heartbeat FROM service_health WHERE service_name = '{SERVICE_NAME}'"
    
    try:
        response = requests.post(QUERY_ENDPOINT, json={"query": query}, timeout=10)
        response.raise_for_status()
        data = response.json()

        if not data or 'results' not in data or not data['results']:
            print(f"FAIL: No heartbeat record found for {SERVICE_NAME}")
            sys.exit(1)

        last_heartbeat_str = data['results'][0]['last_heartbeat']
        # Assuming ISO format timestamp
        last_heartbeat = datetime.fromisoformat(last_heartbeat_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        
        age = now - last_heartbeat
        
        if age < timedelta(minutes=FRESHNESS_THRESHOLD_MINUTES):
            print("PASS")
            sys.exit(0)
        else:
            print(f"FAIL: Heartbeat stale. Last heartbeat: {last_heartbeat_str}, Age: {age}")
            sys.exit(1)

    except Exception as e:
        print(f"FAIL: Error querying service_health: {e}")
        sys.exit(1)

if __name__ == "__main__":
    verify_heartbeat()