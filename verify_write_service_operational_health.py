import requests
import time
import random

def verify_health() -> bool:
    base_url = "http://127.0.0.1:8772"
    max_retries = 3
    initial_backoff = 1  # seconds
    max_backoff = 10  # seconds

    def exponential_backoff(retry_count):
        backoff = min(initial_backoff * (2 ** retry_count), max_backoff)
        time.sleep(backoff + random.uniform(0, 1))  # Add jitter

    def make_request(method, endpoint, data=None, params=None):
        url = f"{base_url}{endpoint}"
        try:
            response = requests.request(
                method,
                url,
                json=data,
                params=params,
                timeout=10
            )
            return response
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}")
            return None

    # Check health endpoint if available
    for retry in range(max_retries):
        response = make_request("GET", "/health")
        if response is not None and response.status_code == 200:
            print("Health endpoint check passed.")
            break
        if retry < max_retries - 1:
            exponential_backoff(retry)
    else:
        print("Health endpoint check failed.")
        return False

    # Check query endpoint
    for retry in range(max_retries):
        response = make_request("POST", "/query", data={"query": "SELECT 1"})
        if response is not None and response.status_code == 200:
            print("Query endpoint check passed.")
            break
        if retry < max_retries - 1:
            exponential_backoff(retry)
    else:
        print("Query endpoint check failed.")
        return False

    # Check write endpoint
    test_service = f"test_service_{int(time.time())}"
    for retry in range(max_retries):
        response = make_request(
            "POST",
            "/write",
            data={
                "query": f"INSERT INTO service_health (service, status) VALUES ('{test_service}', 'healthy')"
            }
        )
        if response is not None and response.status_code == 200:
            print("Write endpoint check passed.")
            break
        if retry < max_retries - 1:
            exponential_backoff(retry)
    else:
        print("Write endpoint check failed.")
        return False

    return True

if __name__ == '__main__':
    if verify_health():
        print("PASS")
    else:
        print("FAIL")