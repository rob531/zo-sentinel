import os
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# --- Configuration ---
# The URL for the mcp_definition_history_populator's internal trigger endpoint.
# It's good practice to make this configurable, e.g., via environment variables.
# For this example, we use an environment variable with a hardcoded default.
POPULATOR_TRIGGER_URL = os.getenv(
    "MCP_DEFINITION_HISTORY_POPULATOR_URL",
    "http://127.0.0.1:8773/trigger_population"
)

app = FastAPI(
    title="MCP Definition History Trigger API",
    description="API to manually trigger the MCP Definition History Populator daemon.",
    version="1.0.0"
)

# --- Request Body Model ---
class TriggerRequest(BaseModel):
    """
    Request body for triggering the MCP Definition History Populator.
    """
    mcp_id: Optional[str] = None

# --- API Endpoint ---
@app.post(
    "/trigger_mcp_definition_history_population",
    summary="Trigger MCP Definition History Population",
    response_model=dict,
    status_code=200
)
async def trigger_mcp_definition_history_population(request_body: TriggerRequest):
    """
    Triggers the `mcp_definition_history_populator` daemon to update its data.

    - If `mcp_id` is provided in the request body, a targeted update for that specific MCP
      will be initiated by the populator.
    - If no `mcp_id` is provided (empty body or `mcp_id: null`), a full re-population
      of all MCP definition history will be initiated by the populator.

    Returns:
    - `200 OK`: If the trigger request was successfully sent to the populator.
      Response body: `{'status': 'triggered', 'message': 'Population initiated'}`
    - `503 Service Unavailable`: If the trigger API cannot connect to the populator.
    - `504 Gateway Timeout`: If the populator does not respond within the timeout period.
    - `500 Internal Server Error`: For other unexpected errors or if the populator
      returns an error status.
    """
    payload = {}
    if request_body.mcp_id:
        payload["mcp_id"] = request_body.mcp_id

    try:
        # Send an internal HTTP POST request to the mcp_definition_history_populator
        # A timeout is crucial for external HTTP calls to prevent hanging.
        response = requests.post(POPULATOR_TRIGGER_URL, json=payload, timeout=10)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)

        # If the populator successfully received the trigger, return success.
        return {"status": "triggered", "message": "Population initiated"}

    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to connect to the mcp_definition_history_populator at {POPULATOR_TRIGGER_URL}. "
                   "Please ensure the populator daemon is running and accessible."
        )
    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=504,
            detail=f"Timeout while connecting to the mcp_definition_history_populator at {POPULATOR_TRIGGER_URL}. "
                   "The populator might be overloaded or slow to respond."
        )
    except requests.exceptions.HTTPError as e:
        # Catch HTTP errors returned by the populator itself (e.g., 400, 500 from populator)
        status_code = e.response.status_code if e.response is not None else 500
        detail_message = f"Error from mcp_definition_history_populator (status {status_code}): {e}"
        if e.response and e.response.text:
            detail_message += f" Populator response: {e.response.text}"
        raise HTTPException(
            status_code=status_code,
            detail=detail_message
        )
    except requests.exceptions.RequestException as e:
        # Catch any other requests-related errors not covered above
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred during the request to the populator: {e}"
        )
    except Exception as e:
        # Catch any other unexpected errors
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected internal server error occurred: {e}"
        )

# --- Acceptance Test (in __main__ block) ---
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    import unittest.mock as mock
    import sys

    # Create a test client for the FastAPI app
    client = TestClient(app)
    all_tests_passed = True

    print("\n--- Running Acceptance Tests for MCP Definition History Trigger API ---")

    # Test Case 1: Trigger full re-population (empty body)
    print("\n[TEST 1] Testing full re-population (empty request body)...")
    with mock.patch("requests.post") as mock_post:
        # Configure the mock to return a successful response from the populator
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None  # No exception on success
        mock_post.return_value = mock_response

        response = client.post("/trigger_mcp_definition_history_population", json={})

        # Assertions
        try:
            assert response.status_code == 200, f"Expected status 200, got {response.status_code}"
            assert response.json() == {"status": "triggered", "message": "Population initiated"}, \
                f"Expected success message, got {response.json()}"
            mock_post.assert_called_once_with(POPULATOR_TRIGGER_URL, json={}, timeout=10)
            print("  [PASS] Full re-population test successful.")
        except AssertionError as e:
            print(f"  [FAIL] Full re-population test failed: {e}")
            all_tests_passed = False

    # Test Case 2: Trigger targeted update (with mcp_id)
    test_mcp_id = "mcp-123-abc"
    print(f"\n[TEST 2] Testing targeted update for mcp_id: '{test_mcp_id}'...")
    with mock.patch("requests.post") as mock_post:
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        response = client.post("/trigger_mcp_definition_history_population", json={"mcp_id": test_mcp_id})

        # Assertions
        try:
            assert response.status_code == 200, f"Expected status 200, got {response.status_code}"
            assert response.json() == {"status": "triggered", "message": "Population initiated"}, \
                f"Expected success message, got {response.json()}"
            mock_post.assert_called_once_with(POPULATOR_TRIGGER_URL, json={"mcp_id": test_mcp_id}, timeout=10)
            print("  [PASS] Targeted update test successful.")
        except AssertionError as e:
            print(f"  [FAIL] Targeted update test failed: {e}")
            all_tests_passed = False

    # Test Case 3: Populator returns an error (e.g., 500 Internal Server Error)
    print("\n[TEST 3] Testing populator returning an internal server error (500)...")
    with mock.patch("requests.post") as mock_post:
        mock_response = mock.Mock()
        mock_response.status_code = 500
        mock_response.text = "Populator encountered an internal error."
        # Configure raise_for_status to raise an HTTPError
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "500 Server Error: Internal Populator Error", response=mock_response
        )
        mock_post.return_value = mock_response

        response = client.post("/trigger_mcp_definition_history_population", json={})

        # Assertions
        try:
            assert response.status_code == 500, f"Expected status 500, got {response.status_code}"
            assert "Error from mcp_definition_history_populator (status 500)" in response.json()["detail"]
            assert "Populator response: Populator encountered an internal error." in response.json()["detail"]
            mock_post.assert_called_once_with(POPULATOR_TRIGGER_URL, json={}, timeout=10)
            print("  [PASS] Populator internal error test successful.")
        except AssertionError as e:
            print(f"  [FAIL] Populator internal error test failed: {e}")
            all_tests_passed = False

    # Test Case 4: Populator connection error (e.g., populator is down)
    print("\n[TEST 4] Testing populator connection error (e.g., populator is down)...")
    with mock.patch("requests.post") as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused by populator")

        response = client.post("/trigger_mcp_definition_history_population", json={})

        # Assertions
        try:
            assert response.status_code == 503, f"Expected status 503, got {response.status_code}"
            assert "Failed to connect to the mcp_definition_history_populator" in response.json()["detail"]
            mock_post.assert_called_once_with(POPULATOR_TRIGGER_URL, json={}, timeout=10)
            print("  [PASS] Populator connection error test successful.")
        except AssertionError as e:
            print(f"  [FAIL] Populator connection error test failed: {e}")
            all_tests_passed = False

    # Test Case 5: Populator timeout
    print("\n[TEST 5] Testing populator timeout...")
    with mock.patch("requests.post") as mock_post:
        mock_post.side_effect = requests.exceptions.Timeout("Populator did not respond in time")

        response = client.post("/trigger_mcp_definition_history_population", json={})

        # Assertions
        try:
            assert response.status_code == 504, f"Expected status 504, got {response.status_code}"
            assert "Timeout while connecting to the mcp_definition_history_populator" in response.json()["detail"]
            mock_post.assert_called_once_with(POPULATOR_TRIGGER_URL, json={}, timeout=10)
            print("  [PASS] Populator timeout test successful.")
        except AssertionError as e:
            print(f"  [FAIL] Populator timeout test failed: {e}")
            all_tests_passed = False

    print("\n--- Acceptance Test Summary ---")
    if all_tests_passed:
        print("All acceptance tests passed successfully! PASS")
    else:
        print("Some acceptance tests failed. FAIL")
        sys.exit(1) # Indicate failure to the shell