import os
import requests
from fastapi import FastAPI, APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from typing import List, Dict, Any

# Define the FastAPI router
router = APIRouter()

# Configuration for the write_service base URL.
# In a real application, this would be configured via environment variables or a config file.
# For this example, we'll use an environment variable with a default.
WRITE_SERVICE_BASE_URL = os.getenv("WRITE_SERVICE_BASE_URL", "http://localhost:8001")

@router.get(
    "/mcp_definition_history/report",
    response_model=List[Dict[str, Any]], # Expecting a list of dictionaries
    summary="Get recent MCP definition history report",
    description="Retrieves a list of recent definition changes for the MCP ecosystem from the mcp_definition_history table via the write_service.",
    tags=["MCP Definition History"]
)
async def get_definition_history_report() -> List[Dict[str, Any]]:
    """
    Reads the mcp_definition_history table via the app DB session (write_service query)
    and returns a JSON list of recent definition changes for the MCP ecosystem.

    The query is performed by making an HTTP POST request to the configured
    `write_service` which is responsible for database interaction.

    Returns:
        list[dict]: A list of dictionaries, each representing a definition change
                    with keys: mcp_name, change_type, timestamp, author.

    Raises:
        HTTPException: If there's an issue communicating with the write_service
                       or if the write_service returns an error or unexpected data format.
    """
    # SQL query to retrieve definition history.
    # The 'ORDER BY timestamp DESC' ensures recent changes are prioritized,
    # though the prompt implies reading the whole table.
    sql_query = """
        SELECT mcp_name, change_type, timestamp, author
        FROM mcp_definition_history
        ORDER BY timestamp DESC;
    """
    
    # Construct the full URL for the write_service query endpoint.
    # We assume the write_service exposes a '/query' endpoint for executing SQL.
    write_service_query_url = f"{WRITE_SERVICE_BASE_URL}/query"
    
    try:
        # Make a POST request to the write_service with the SQL query in the JSON body.
        # A timeout is included to prevent indefinite waits.
        response = requests.post(write_service_query_url, json={"query": sql_query}, timeout=10)
        
        # Raise an HTTPError for bad responses (4xx or 5xx status codes).
        response.raise_for_status()
        
        # Parse the JSON response from the write_service.
        data = response.json()
        
        # The write_service might return results under a specific key (e.g., "results")
        # or as a direct list. We handle both possibilities.
        if "results" in data and isinstance(data["results"], list):
            report_data = data["results"]
        elif isinstance(data, list):
            report_data = data
        else:
            # If the response format is unexpected, raise an internal server error.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected response format from write_service: {data}"
            )

        # Validate that each item in the report_data contains the required keys.
        required_keys = ["mcp_name", "change_type", "timestamp", "author"]
        for item in report_data:
            if not all(k in item for k in required_keys):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Write service returned data with missing required keys."
                )
        
        return report_data
        
    except requests.exceptions.ConnectionError as e:
        # Handle network-related errors (e.g., service not reachable).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not connect to write_service at {WRITE_SERVICE_BASE_URL}: {e}"
        )
    except requests.exceptions.Timeout as e:
        # Handle cases where the request to the write_service times out.
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Timeout connecting to write_service at {WRITE_SERVICE_BASE_URL}: {e}"
        )
    except requests.exceptions.HTTPError as e:
        # Handle HTTP errors returned by the write_service itself.
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Error from write_service: {e.response.text}"
        )
    except Exception as e:
        # Catch any other unexpected errors during the process.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred while fetching report: {e}"
        )

# Main block for acceptance testing using FastAPI's TestClient.
# This block will only run when the script is executed directly.
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from unittest.mock import patch, Mock
    import json

    # Create a FastAPI app instance and include the router.
    app = FastAPI()
    app.include_router(router)

    # Create a TestClient for making requests to the FastAPI app.
    client = TestClient(app)

    # --- Test Case 1: Successful Report Retrieval ---
    # Mock data that the write_service would return.
    mock_db_data = [
        {"mcp_name": "MCP_A", "change_type": "CREATE", "timestamp": "2023-01-01T10:00:00Z", "author": "user1"},
        {"mcp_name": "MCP_B", "change_type": "UPDATE", "timestamp": "2023-01-02T11:00:00Z", "author": "user2"},
        {"mcp_name": "MCP_A", "change_type": "DELETE", "timestamp": "2023-01-03T12:00:00Z", "author": "user1"},
    ]

    # Use unittest.mock.patch to intercept calls to requests.post.
    # This prevents actual network calls and allows simulating the write_service response.
    with patch('requests.post') as mock_post:
        # Configure the mock response object to simulate a successful API call.
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": mock_db_data} # Simulate service returning data under "results" key
        mock_response.raise_for_status.return_value = None # Simulate no HTTP errors
        
        mock_post.return_value = mock_response

        print("Running acceptance test for /mcp_definition_history/report (success)...")
        response = client.get("/mcp_definition_history/report")

        # Assertions for a successful response.
        assert response.status_code == 200, f"Expected status code 200, got {response.status_code}"
        
        response_json = response.json()
        assert isinstance(response_json, list), f"Expected a list, got {type(response_json)}"
        assert len(response_json) == len(mock_db_data), f"Expected {len(mock_db_data)} items, got {len(response_json)}"
        
        # Verify each item in the response has the expected structure and content.
        for i, item in enumerate(response_json):
            assert isinstance(item, dict), f"Expected dict at index {i}, got {type(item)}"
            assert "mcp_name" in item, f"Missing 'mcp_name' in item {i}"
            assert "change_type" in item, f"Missing 'change_type' in item {i}"
            assert "timestamp" in item, f"Missing 'timestamp' in item {i}"
            assert "author" in item, f"Missing 'author' in item {i}"
            
            # Check if the item is present in the mock data.
            # Note: For exact order-sensitive tests, a more precise comparison would be needed.
            assert item in mock_db_data, f"Item {item} not found in mock data"

        print("PASS: /mcp_definition_history/report endpoint works as expected (success case).")

    # --- Test Case 2: write_service returns an HTTP error (e.g., 500) ---
    with patch('requests.post') as mock_post_error:
        mock_error_response = Mock()
        mock_error_response.status_code = 500
        mock_error_response.text = "Internal Server Error from write_service"
        # Configure raise_for_status to raise an HTTPError.
        mock_error_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "500 Server Error: Internal Server Error from write_service", response=mock_error_response
        )
        mock_post_error.return_value = mock_error_response

        print("\nRunning acceptance test for write_service HTTP error handling...")
        response_error = client.get("/mcp_definition_history/report")
        assert response_error.status_code == 500, f"Expected status code 500 for error, got {response_error.status_code}"
        assert "Error from write_service" in response_error.json()["detail"]
        print("PASS: Error handling for write_service HTTPError works as expected.")

    # --- Test Case 3: write_service connection error ---
    with patch('requests.post') as mock_post_conn_error:
        # Configure requests.post to raise a ConnectionError.
        mock_post_conn_error.side_effect = requests.exceptions.ConnectionError("Connection refused")

        print("\nRunning acceptance test for write_service connection error handling...")
        response_conn_error = client.get("/mcp_definition_history/report")
        assert response_conn_error.status_code == 503, f"Expected status code 503 for connection error, got {response_conn_error.status_code}"
        assert "Could not connect to write_service" in response_conn_error.json()["detail"]
        print("PASS: Error handling for write_service ConnectionError works as expected.")

    # --- Test Case 4: write_service returns unexpected JSON format ---
    with patch('requests.post') as mock_post_bad_format:
        mock_bad_format_response = Mock()
        mock_bad_format_response.status_code = 200
        mock_bad_format_response.json.return_value = {"status": "success", "message": "no results key"} # Missing "results" or not a list
        mock_bad_format_response.raise_for_status.return_value = None
        mock_post_bad_format.return_value = mock_bad_format_response

        print("\nRunning acceptance test for write_service unexpected format handling...")
        response_bad_format = client.get("/mcp_definition_history/report")
        assert response_bad_format.status_code == 500, f"Expected status code 500 for bad format, got {response_bad_format.status_code}"
        assert "Unexpected response format from write_service" in response_bad_format.json()["detail"]
        print("PASS: Error handling for write_service unexpected format works as expected.")

    # --- Test Case 5: write_service returns data with missing required keys ---
    with patch('requests.post') as mock_post_missing_keys:
        mock_missing_keys_data = [
            {"mcp_name": "MCP_C", "change_type": "CREATE", "timestamp": "2023-01-04T13:00:00Z"}, # Missing 'author'
        ]
        mock_missing_keys_response = Mock()
        mock_missing_keys_response.status_code = 200
        mock_missing_keys_response.json.return_value = {"results": mock_missing_keys_data}
        mock_missing_keys_response.raise_for_status.return_value = None
        mock_post_missing_keys.return_value = mock_missing_keys_response

        print("\nRunning acceptance test for write_service data with missing keys...")
        response_missing_keys = client.get("/mcp_definition_history/report")
        assert response_missing_keys.status_code == 500, f"Expected status code 500 for missing keys, got {response_missing_keys.status_code}"
        assert "Write service returned data with missing required keys." in response_missing_keys.json()["detail"]
        print("PASS: Error handling for write_service data with missing keys works as expected.")