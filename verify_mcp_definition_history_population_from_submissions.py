import requests
from typing import Dict, Tuple

def verify_mcp_definition_history_population(write_service_url: str) -> Tuple[bool, Dict]:
    """
    Verify that the `mcp_definition_history` table is correctly populated from `mcp_submissions`.

    Args:
        write_service_url: URL of the write service's query endpoint.

    Returns:
        Tuple[bool, Dict]: (success, evidence)
            - success: True if verification passes, False otherwise.
            - evidence: Dictionary containing counts and samples for verification.
    """
    evidence = {}

    # Query mcp_submissions
    submissions_query = """
    SELECT COUNT(*) as count, ARRAY_AGG(DISTINCT mcp_name) as mcp_names
    FROM mcp_submissions
    """
    submissions_response = requests.post(f"{write_service_url}/query", json={"query": submissions_query}).json()
    submissions_count = submissions_response["rows"][0]["count"]
    submissions_mcp_names = submissions_response["rows"][0]["mcp_names"]

    # Query mcp_definition_history
    history_query = """
    SELECT COUNT(*) as count, ARRAY_AGG(DISTINCT mcp_name) as mcp_names
    FROM mcp_definition_history
    """
    history_response = requests.post(f"{write_service_url}/query", json={"query": history_query}).json()
    history_count = history_response["rows"][0]["count"]
    history_mcp_names = history_response["rows"][0]["mcp_names"]

    # Populate evidence
    evidence.update({
        "submissions_count": submissions_count,
        "submissions_mcp_names": submissions_mcp_names,
        "history_count": history_count,
        "history_mcp_names": history_mcp_names,
    })

    # Verification logic
    success = True
    if submissions_count > 0 and history_count == 0:
        success = False
    elif submissions_count > 0 and set(submissions_mcp_names) != set(history_mcp_names):
        success = False

    return success, evidence

if __name__ == "__main__":
    # Simulate write_service responses
    class MockResponse:
        def __init__(self, json_data):
            self.json_data = json_data

        def json(self):
            return self.json_data

    # Test case 1: mcp_submissions has entries, mcp_definition_history is empty
    def mock_requests_post_empty_history(url, json):
        if "mcp_submissions" in json["query"]:
            return MockResponse({
                "rows": [{"count": 2, "mcp_names": ["mcp1", "mcp2"]}]
            })
        else:
            return MockResponse({
                "rows": [{"count": 0, "mcp_names": []}]
            })

    requests.post = mock_requests_post_empty_history
    success, evidence = verify_mcp_definition_history_population("http://mock_write_service")
    assert not success
    assert evidence["submissions_count"] == 2
    assert evidence["history_count"] == 0

    # Test case 2: Both tables have matching entries
    def mock_requests_post_populated_history(url, json):
        if "mcp_submissions" in json["query"]:
            return MockResponse({
                "rows": [{"count": 2, "mcp_names": ["mcp1", "mcp2"]}]
            })
        else:
            return MockResponse({
                "rows": [{"count": 2, "mcp_names": ["mcp1", "mcp2"]}]
            })

    requests.post = mock_requests_post_populated_history
    success, evidence = verify_mcp_definition_history_population("http://mock_write_service")
    assert success
    assert evidence["submissions_count"] == 2
    assert evidence["history_count"] == 2

    print("PASS")