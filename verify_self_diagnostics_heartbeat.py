import requests
import time
from datetime import datetime, timedelta

def run():
    try:
        # Query the service_health table via the write_service HTTP endpoint
        response = requests.post(
            "http://localhost:8000/write_service",
            json={
                "query": "SELECT last_heartbeat FROM service_health WHERE service_name = 'self_diagnostics'"
            }
        )
        response.raise_for_status()
        data = response.json()

        if not data or 'last_heartbeat' not in data[0]:
            print("FAIL: No heartbeat data found")
            return 1

        last_heartbeat = datetime.fromisoformat(data[0]['last_heartbeat'])
        current_time = datetime.now()
        heartbeat_age = (current_time - last_heartbeat).total_seconds()

        if heartbeat_age <= 600:
            print(f"PASS: Heartbeat is fresh (age: {heartbeat_age:.2f} seconds)")
            return 0
        else:
            print(f"FAIL: Heartbeat is stale (age: {heartbeat_age:.2f} seconds)")
            return 1

    except requests.exceptions.RequestException as e:
        print(f"FAIL: Error querying write_service: {e}")
        return 1

if __name__ == "__main__":
    # Mocking requests.post for testing
    def mock_post(url, json):
        if url == "http://localhost:8000/write_service":
            if "fresh" in json['query']:
                return type('Response', (), {
                    'json': lambda: [{'last_heartbeat': (datetime.now() - timedelta(seconds=300)).isoformat()}],
                    'raise_for_status': lambda: None,
                    'status_code': 200
                })()
            elif "stale" in json['query']:
                return type('Response', (), {
                    'json': lambda: [{'last_heartbeat': (datetime.now() - timedelta(seconds=900)).isoformat()}],
                    'raise_for_status': lambda: None,
                    'status_code': 200
                })()
            else:
                return type('Response', (), {
                    'json': lambda: [],
                    'raise_for_status': lambda: None,
                    'status_code': 200
                })()
        else:
            raise Exception("Unexpected URL")

    # Simulate fresh heartbeat
    requests.post = mock_post
    print("Testing fresh heartbeat...")
    result = run()
    if result != 0:
        print("FAIL: Fresh heartbeat test failed")
        exit(1)

    # Simulate stale heartbeat
    print("\nTesting stale heartbeat...")
    result = run()
    if result != 1:
        print("FAIL: Stale heartbeat test failed")
        exit(1)

    # Simulate no heartbeat data
    print("\nTesting no heartbeat data...")
    result = run()
    if result != 1:
        print("FAIL: No heartbeat data test failed")
        exit(1)

    print("\nPASS: All simulated scenarios passed")