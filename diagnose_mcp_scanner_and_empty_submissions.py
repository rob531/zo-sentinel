import requests
from datetime import datetime, timedelta

# Configuration
WRITE_SERVICE_URL = "http://write_service:8080/query"
STALE_THRESHOLD_MINUTES = 30

def query_write_service(query):
    """Query the write_service endpoint and return the result."""
    response = requests.post(WRITE_SERVICE_URL, json={"query": query})
    response.raise_for_status()
    return response.json()

def is_mcp_scanner_stale():
    """Check if mcp_scanner is stale based on last_heartbeat."""
    query = """
    SELECT last_heartbeat
    FROM service_health
    WHERE service_name = 'mcp_scanner'
    """
    result = query_write_service(query)
    if not result or not result['data']:
        return True  # Assume stale if no data

    last_heartbeat = datetime.fromisoformat(result['data'][0]['last_heartbeat'])
    stale_threshold = datetime.now() - timedelta(minutes=STALE_THRESHOLD_MINUTES)
    return last_heartbeat < stale_threshold

def is_mcp_submissions_empty():
    """Check if mcp_submissions table is empty."""
    query = "SELECT COUNT(*) as count FROM mcp_submissions"
    result = query_write_service(query)
    if not result or not result['data']:
        return True  # Assume empty if no data

    return result['data'][0]['count'] == 0

def diagnose():
    """Diagnose the health of mcp_scanner and mcp_submissions."""
    issues = []

    if is_mcp_scanner_stale():
        issues.append("mcp_scanner is stale (last heartbeat older than {} minutes).".format(STALE_THRESHOLD_MINUTES))
        issues.append("Potential root causes:")
        issues.append("- mcp_scanner daemon is not running or crashed.")
        issues.append("- Network issues preventing heartbeats.")
        issues.append("- Upstream data ingestion issues causing mcp_scanner to stall.")

    if is_mcp_submissions_empty():
        issues.append("mcp_submissions table is empty.")
        issues.append("Potential root causes:")
        issues.append("- Upstream data ingestion issues (no data being written).")
        issues.append("- mcp_scanner daemon is not processing data correctly.")
        issues.append("- Database corruption or misconfiguration.")

    if issues:
        print("\n".join(issues))
    else:
        print("No issues detected with mcp_scanner or mcp_submissions.")

if __name__ == "__main__":
    # Simulate different scenarios for testing
    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self.json_data = json_data
            self.status_code = status_code

        def json(self):
            return self.json_data

        def raise_for_status(self):
            if self.status_code != 200:
                raise requests.exceptions.HTTPError("Mock HTTP Error")

    # Scenario 1: mcp_scanner stale and mcp_submissions empty
    def mock_requests_post_stale_and_empty(url, json):
        if "service_health" in json['query']:
            return MockResponse({
                "data": [{"last_heartbeat": (datetime.now() - timedelta(hours=2)).isoformat()}]
            })
        elif "mcp_submissions" in json['query']:
            return MockResponse({"data": [{"count": 0}]})
        return MockResponse({"data": []})

    requests.post = mock_requests_post_stale_and_empty
    print("Scenario 1: mcp_scanner stale and mcp_submissions empty")
    diagnose()
    print("\n")

    # Scenario 2: mcp_scanner healthy but mcp_submissions empty
    def mock_requests_post_healthy_and_empty(url, json):
        if "service_health" in json['query']:
            return MockResponse({
                "data": [{"last_heartbeat": datetime.now().isoformat()}]
            })
        elif "mcp_submissions" in json['query']:
            return MockResponse({"data": [{"count": 0}]})
        return MockResponse({"data": []})

    requests.post = mock_requests_post_healthy_and_empty
    print("Scenario 2: mcp_scanner healthy but mcp_submissions empty")
    diagnose()
    print("\n")

    # Scenario 3: mcp_scanner stale but mcp_submissions not empty
    def mock_requests_post_stale_and_not_empty(url, json):
        if "service_health" in json['query']:
            return MockResponse({
                "data": [{"last_heartbeat": (datetime.now() - timedelta(hours=2)).isoformat()}]
            })
        elif "mcp_submissions" in json['query']:
            return MockResponse({"data": [{"count": 100}]})
        return MockResponse({"data": []})

    requests.post = mock_requests_post_stale_and_not_empty
    print("Scenario 3: mcp_scanner stale but mcp_submissions not empty")
    diagnose()
    print("\n")

    # Scenario 4: mcp_scanner healthy and mcp_submissions not empty
    def mock_requests_post_healthy_and_not_empty(url, json):
        if "service_health" in json['query']:
            return MockResponse({
                "data": [{"last_heartbeat": datetime.now().isoformat()}]
            })
        elif "mcp_submissions" in json['query']:
            return MockResponse({"data": [{"count": 100}]})
        return MockResponse({"data": []})

    requests.post = mock_requests_post_healthy_and_not_empty
    print("Scenario 4: mcp_scanner healthy and mcp_submissions not empty")
    diagnose()
    print("\n")