import requests
import time
from datetime import datetime, timedelta

# Configuration
WRITE_SERVICE_URL = "http://write_service:8080"
WRITE_ENDPOINT = f"{WRITE_SERVICE_URL}/write"
QUERY_ENDPOINT = f"{WRITE_SERVICE_URL}/query"
HEARTBEAT_TABLE = "service_health"
AUDIT_LOG_TABLE = "audit_log"
TIMEOUT = 5  # seconds
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds

def send_write_request():
    """Send a test write request to the write_service."""
    payload = {
        "measurement": "test_measurement",
        "tags": {"host": "test_host"},
        "fields": {"value": 1},
        "time": int(time.time())
    }
    try:
        response = requests.post(WRITE_ENDPOINT, json=payload, timeout=TIMEOUT)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"Write request failed: {e}")
        return False

def send_query_request():
    """Send a test query request to the write_service."""
    query = f"SELECT * FROM {HEARTBEAT_TABLE} WHERE time > now() - 10s"
    try:
        response = requests.get(QUERY_ENDPOINT, params={"q": query}, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Query request failed: {e}")
        return None

def check_heartbeat(query_result):
    """Check if the heartbeat is recent."""
    if not query_result or not query_result.get("results"):
        return False
    results = query_result["results"][0]["series"]
    if not results:
        return False
    last_heartbeat = results[0]["values"][-1][0]
    last_heartbeat_time = datetime.fromtimestamp(float(last_heartbeat))
    return (datetime.now() - last_heartbeat_time) < timedelta(seconds=10)

def check_audit_log():
    """Check for recent error logs in the audit_log."""
    query = f"SELECT * FROM {AUDIT_LOG_TABLE} WHERE time > now() - 60s AND level = 'error'"
    try:
        response = requests.get(QUERY_ENDPOINT, params={"q": query}, timeout=TIMEOUT)
        response.raise_for_status()
        query_result = response.json()
        if query_result and query_result.get("results"):
            results = query_result["results"][0]["series"]
            if results and results[0]["values"]:
                return False
        return True
    except requests.exceptions.RequestException as e:
        print(f"Audit log check failed: {e}")
        return False

def verify_write_service_health():
    """Verify the health of the write_service."""
    for attempt in range(MAX_RETRIES):
        print(f"Attempt {attempt + 1} of {MAX_RETRIES}")

        # Send test write request
        if not send_write_request():
            print("Write request failed.")
            time.sleep(RETRY_DELAY)
            continue

        # Send test query request
        query_result = send_query_request()
        if not query_result:
            print("Query request failed.")
            time.sleep(RETRY_DELAY)
            continue

        # Check heartbeat
        if not check_heartbeat(query_result):
            print("Heartbeat check failed.")
            time.sleep(RETRY_DELAY)
            continue

        # Check audit log
        if not check_audit_log():
            print("Audit log check failed.")
            time.sleep(RETRY_DELAY)
            continue

        # All checks passed
        print("PASS: write_service is healthy")
        return True

    # All attempts failed
    print("FAIL: write_service is unhealthy")
    return False

if __name__ == "__main__":
    verify_write_service_health()