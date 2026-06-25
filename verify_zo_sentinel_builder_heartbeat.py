import requests
import time

def verify_heartbeat():
    # Define the service name and stale age
    service_name = "zo_sentinel_builder"
    stale_age = 600  # 600 seconds

    # Query the service_health table for the last heartbeat of zo_sentinel_builder
    url = "http://write_service/service_health"
    params = {"service_name": service_name}
    response = requests.get(url, params=params)

    if response.status_code != 200:
        print("FAIL")
        return False

    data = response.json()
    last_heartbeat = data.get("last_heartbeat")

    if not last_heartbeat:
        print("FAIL")
        return False

    # Calculate the time difference between now and the last heartbeat
    current_time = time.time()
    time_diff = current_time - last_heartbeat

    # Check if the heartbeat is within the expected freshness window
    if time_diff <= stale_age:
        print("PASS")
        return True
    else:
        print("FAIL")
        return False

if __name__ == '__main__':
    success = verify_heartbeat()
    exit_code = 0 if success else 1
    exit(exit_code)