import datetime
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any

# Initialize FastAPI app
app = FastAPI()

# Configuration for the write service
WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"

# Pydantic model for the response data
class MCPDefinitionHistoryDetail(BaseModel):
    definition_id: str
    mcp_name: str
    version: int
    changes: str
    timestamp: datetime.datetime
    source: str

@app.get(
    "/mcp/definition_history/{definition_id}",
    response_model=MCPDefinitionHistoryDetail,
    summary="Retrieve detailed historical information for a specific MCP definition",
    response_description="Detailed history for the requested MCP definition"
)
async def get_mcp_definition_history_detail(definition_id: str):
    """
    Retrieves comprehensive historical details for a specific MCP definition.

    - **definition_id**: The unique identifier of the MCP definition.
    """
    sql_query = """
        SELECT definition_id, mcp_name, version, changes, timestamp, source
        FROM mcp_definition_history
        WHERE definition_id = :definition_id
    """
    payload = {
        "sql": sql_query,
        "params": {"definition_id": definition_id}
    }

    try:
        response = requests.post(WRITE_SERVICE_URL, json=payload)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        response_data = response.json()

        if not response_data or not response_data.get("data"):
            raise HTTPException(status_code=404, detail="MCP definition history not found")

        # Assuming the write_service returns a list of dictionaries
        # and we only expect one result for a specific definition_id
        history_record = response_data["data"][0]

        # Pydantic will automatically validate and convert types (e.g., ISO string to datetime)
        return MCPDefinitionHistoryDetail(**history_record)

    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot connect to write service")
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="Write service connection timed out")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error querying write service: {e}")
    except Exception as e:
        # Catch any other unexpected errors
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from unittest.mock import patch

    client = TestClient(app)

    # --- Mocking requests.post for testing ---
    # This class simulates the response object from the 'requests' library
    class MockResponse:
        def __init__(self, json_data: Dict[str, Any], status_code: int = 200):
            self._json_data = json_data
            self.status_code = status_code

        def json(self) -> Dict[str, Any]:
            return self._json_data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.exceptions.HTTPError(f"HTTP Error: {self.status_code}")

    # Mock data that the 'write_service' would return
    mock_db_data = {
        "mcp-def-123": {
            "definition_id": "mcp-def-123",
            "mcp_name": "Example MCP Definition",
            "version": 1,
            "changes": "Initial creation of the definition.",
            "timestamp": datetime.datetime(2023, 10, 26, 10, 30, 0).isoformat(),
            "source": "user_upload",
        },
        "mcp-def-456": {
            "definition_id": "mcp-def-456",
            "mcp_name": "Another MCP",
            "version": 2,
            "changes": "Updated parameters for performance.",
            "timestamp": datetime.datetime(2023, 11, 15, 14, 0, 0).isoformat(),
            "source": "system_update",
        },
    }

    # This function will replace requests.post during tests
    def mock_requests_post(*args, **kwargs):
        if "json" in kwargs and "params" in kwargs["json"]:
            requested_id = kwargs["json"]["params"].get("definition_id")
            if requested_id in mock_db_data:
                return MockResponse({"data": [mock_db_data[requested_id]]})
            else:
                return MockResponse({"data": []}) # Simulate no data found
        return MockResponse({"data": []}) # Default for unexpected calls

    print("Running acceptance tests...")

    # Use patch to replace requests.post with our mock function for the duration of the tests
    with patch("requests.post", side_effect=mock_requests_post) as mock_post:
        # Test Case 1: Known definition_id - Expect 200 OK and correct data
        known_id = "mcp-def-123"
        expected_response_data = mock_db_data[known_id]
        response = client.get(f"/mcp/definition_history/{known_id}")

        assert response.status_code == 200, \
            f"Test 1 Failed: Expected status 200 for known ID, got {response.status_code}"
        assert response.json() == expected_response_data, \
            f"Test 1 Failed: Expected data {expected_response_data}, got {response.json()}"
        print(f"Test 1 (Known ID '{known_id}'): PASSED")

        # Test Case 2: Unknown definition_id - Expect 404 Not Found
        unknown_id = "mcp-def-999"
        response = client.get(f"/mcp/definition_history/{unknown_id}")

        assert response.status_code == 404, \
            f"Test 2 Failed: Expected status 404 for unknown ID, got {response.status_code}"
        assert response.json() == {"detail": "MCP definition history not found"}, \
            f"Test 2 Failed: Expected 404 detail, got {response.json()}"
        print(f"Test 2 (Unknown ID '{unknown_id}'): PASSED")

    print("All acceptance tests PASSED")

    # To run the FastAPI application normally (e.g., with uvicorn):
    # uvicorn mcp_definition_history_detail_api:app --reload