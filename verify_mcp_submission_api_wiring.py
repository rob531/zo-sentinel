# deps: requests
"""Verification script for mcp_submission_api.

It submits a test MCP via the HTTP API and then queries the `mcp_submissions`
table via the write_service to ensure the entry was recorded.

The script is safe to import (no side‑effects) and runs the verification when
executed as a script.
"""
import json
import time
from typing import Any, Dict

import requests

# Configuration – adjust if the service runs on different ports.
API_URL = "http://127.0.0.1:8780/submit_mcp"
WRITE_SERVICE_QUERY_URL = "http://127.0.0.1:8772/query"
# The write_service expects a JSON payload with keys 'sql' and 'params'.

TEST_SUBMISSION: Dict[str, Any] = {
    "mcp_name": "test_mcp_wiring",
    "requested_by": "goose_tester",
    "mcp_definition_json": {"example": "value"},
}


def submit_test_mcp() -> None:
    """POST the test MCP to the API.

    Raises:
        AssertionError: If the HTTP response is not 200 or the JSON payload does
            not indicate success.
    """
    response = requests.post(API_URL, json=TEST_SUBMISSION, timeout=10)
    assert response.status_code == 200, f"API returned {response.status_code}"
    payload = response.json()
    # The API defined in mcp_submission_api returns a dict with a 'status' key.
    assert payload.get("status") == "success", f"Unexpected payload: {payload}"


def query_mcp_submission() -> Dict[str, Any]:
    """Query the write_service for the test MCP entry.

    Returns:
        The first row matching the test MCP as a dict.
    """
    sql = (
        "SELECT mcp_name, requested_by, submission_timestamp "
        "FROM mcp_submissions WHERE mcp_name = %s"
    )
    payload = {
        "sql": sql,
        "params": [TEST_SUBMISSION["mcp_name"]],
    }
    resp = requests.post(WRITE_SERVICE_QUERY_URL, json=payload, timeout=10)
    assert resp.status_code == 200, f"Write service query failed: {resp.status_code}"
    rows = resp.json().get("rows", [])
    assert isinstance(rows, list), "Write service response missing 'rows' list"
    assert rows, "No rows returned for test MCP"
    return rows[0]


def main() -> None:
    # Give the API a moment to start if this script runs immediately after a
    # service launch. In a CI environment the service should already be up.
    time.sleep(0.5)
    submit_test_mcp()
    row = query_mcp_submission()
    # Basic validation of the returned fields.
    assert row.get("mcp_name") == TEST_SUBMISSION["mcp_name"], "mcp_name mismatch"
    assert row.get("requested_by") == TEST_SUBMISSION["requested_by"], "requested_by mismatch"
    # The timestamp should be a non‑empty string (ISO‑8601 is typical).
    ts = row.get("submission_timestamp")
    assert isinstance(ts, str) and ts, "submission_timestamp missing or empty"
    print("PASS")


if __name__ == "__main__":
    main()
