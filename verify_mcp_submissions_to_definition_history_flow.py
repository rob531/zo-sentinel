import requests
import time
import sys
import json

# Configuration
WRITE_SERVICE_URL = "http://write-service:8080"
MCP_SUBMISSIONS_TABLE = "mcp_submissions"
MCP_DEFINITION_HISTORY_TABLE = "mcp_definition_history"
SUBMISSION_DATA = {
    "submission_id": "test_submission_123",
    "definition_id": "test_definition_456",
    "submission_data": {"key": "value"},
    "timestamp": "2023-01-01T00:00:00Z"
}

def simulate_submission():
    """Simulate a new submission by inserting into mcp_submissions table."""
    endpoint = f"{WRITE_SERVICE_URL}/execute"
    payload = {
        "query": f"INSERT INTO {MCP_SUBMISSIONS_TABLE} VALUES (?, ?, ?, ?)",
        "params": [
            SUBMISSION_DATA["submission_id"],
            SUBMISSION_DATA["definition_id"],
            json.dumps(SUBMISSION_DATA["submission_data"]),
            SUBMISSION_DATA["timestamp"]
        ]
    }
    response = requests.post(endpoint, json=payload)
    if response.status_code != 200:
        print(f"FAIL: Failed to simulate submission. Status code: {response.status_code}, Response: {response.text}")
        sys.exit(1)
    print("Submission simulated successfully.")

def verify_definition_history():
    """Verify that the submission led to an entry in mcp_definition_history."""
    time.sleep(5)  # Wait for the processing to complete

    endpoint = f"{WRITE_SERVICE_URL}/query"
    payload = {
        "query": f"SELECT * FROM {MCP_DEFINITION_HISTORY_TABLE} WHERE definition_id = ? AND submission_id = ?",
        "params": [
            SUBMISSION_DATA["definition_id"],
            SUBMISSION_DATA["submission_id"]
        ]
    }
    response = requests.post(endpoint, json=payload)
    if response.status_code != 200:
        print(f"FAIL: Failed to query definition history. Status code: {response.status_code}, Response: {response.text}")
        sys.exit(1)

    data = response.json()
    if not data:
        print("FAIL: No matching entry found in mcp_definition_history.")
        sys.exit(1)

    print("PASS: Verified entry in mcp_definition_history.")
    sys.exit(0)

if __name__ == "__main__":
    simulate_submission()
    verify_definition_history()