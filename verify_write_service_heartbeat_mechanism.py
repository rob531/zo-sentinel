import requests
import time
from datetime import datetime, timedelta

def verify_write_service_heartbeat():
    # Configuration
    write_service_url = "http://localhost:8000/query"
    service_name = "write_service"
    max_heartbeat_age_minutes = 5

    # Query the service_health table
    query = f"""
    SELECT timestamp
    FROM service_health
    WHERE service_name = '{service_name}'
    ORDER BY timestamp DESC
    LIMIT 1
    """
    params = {'q': query}

    try:
        response = requests.get(write_service_url, params=params)
        response.raise_for_status()
        data = response.json()

        if not data or 'data' not in data or not data['data']:
            raise AssertionError("No heartbeat data found for write_service")

        latest_heartbeat = data['data'][0][0]
        heartbeat_time = datetime.fromisoformat(latest_heartbeat.replace('Z', '+00:00'))
        current_time = datetime.utcnow()
        age = current_time - heartbeat_time

        if age > timedelta(minutes=max_heartbeat_age_minutes):
            raise AssertionError(f"Heartbeat is too old. Last heartbeat was {age.total_seconds()/60:.2f} minutes ago")

        print(f"Latest heartbeat from {service_name} at {heartbeat_time} (age: {age.total_seconds()/60:.2f} minutes)")

    except requests.exceptions.RequestException as e:
        raise AssertionError(f"Failed to query write_service: {str(e)}")

if __name__ == "__main__":
    try:
        verify_write_service_heartbeat()
        print("PASS")
    except AssertionError as e:
        print(f"FAIL: {str(e)}")
        raise