import os
import threading
import time
from typing import List, Dict, Any

import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

# --- FastAPI App Initialization ---
app = FastAPI(
    title="MCP Risk Tiers API",
    description="API to retrieve Model Context Protocol (MCP) server risk tiers and scores.",
    version="1.0.0",
)

# --- Pydantic Models for Response ---
class MCPServerRiskTier(BaseModel):
    """
    Represents the risk tier and overall risk score for an MCP server.
    """
    mcp_name: str
    server_id: str
    risk_tier: str  # e.g., "LOW", "MEDIUM", "HIGH"
    overall_risk: float # A numerical score, typically between 0.0 and 1.0

# --- Configuration ---
# The URL for the internal write_service, configurable via environment variable.
# Default to a common local port for development/testing.
WRITE_SERVICE_URL = os.getenv("WRITE_SERVICE_URL", "http://localhost:8001")

# --- Helper Function for Database Interaction via write_service ---
def query_write_service(sql_query: str) -> List[Dict[str, Any]]:
    """
    Sends a SQL query to the configured write_service and returns the results.
    Raises HTTPException on connection, timeout, or other request errors.
    """
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"query": sql_query},
            timeout=5  # Set a timeout for the request to write_service
        )
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        return response.json()
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail=f"Could not connect to write_service at {WRITE_SERVICE_URL}. "
                   "Please ensure the service is running and accessible."
        )
    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=504,
            detail=f"write_service at {WRITE_SERVICE_URL} timed out after 5 seconds."
        )
    except requests.exceptions.RequestException as e:
        # Catch any other requests-related errors
        raise HTTPException(
            status_code=500,
            detail=f"Error querying write_service: {e}. Response: {getattr(e.response, 'text', 'N/A')}"
        )
    except Exception as e:
        # Catch any other unexpected errors during JSON parsing or processing
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred while processing write_service response: {e}"
        )

# --- FastAPI Endpoint ---
@app.get(
    "/mcps/risk_tiers",
    response_model=List[MCPServerRiskTier],
    summary="Retrieve all MCP server risk tiers and scores",
    description="Fetches a comprehensive list of all Model Context Protocol (MCP) servers "
                "along with their current risk tiers and overall risk scores from the "
                "`mcp_risk_register` and `mcp_server_registry` tables."
)
async def get_all_mcp_risk_tiers() -> List[MCPServerRiskTier]:
    """
    Retrieves a comprehensive list of all Model Context Protocol (MCP) servers
    along with their associated current risk tiers and overall risk scores.
    """
    # Postgres-portable SQL query to join server registry with risk register
    sql_query = """
        SELECT
            msr.mcp_name,
            msr.server_id,
            mrr.risk_tier,
            mrr.overall_risk
        FROM
            mcp_server_registry msr
        JOIN
            mcp_risk_register mrr ON msr.server_id = mrr.server_id;
    """
    
    results = query_write_service(sql_query)
    
    # If no results are returned, an empty list is a valid response.
    if not results:
        return []

    # Validate and convert results to the Pydantic model
    parsed_results = []
    for item in results:
        try:
            parsed_results.append(MCPServerRiskTier(**item))
        except Exception as e:
            # If data from write_service doesn't match the Pydantic model,
            # it indicates a data integrity issue or schema mismatch.
            raise HTTPException(
                status_code=500,
                detail=f"Invalid data format received from write_service for item: {item}. Error: {e}"
            )
            
    return parsed_results

# --- Self-Test Block ---
if __name__ == "__main__":
    print("Starting self-test for mcp_all_risk_tiers_api.py...")

    # --- Mock write_service for testing ---
    # This mock service simulates the behavior of the actual write_service
    # by responding to POST /query requests with predefined test data.
    mock_write_service_app = FastAPI()
    mock_write_service_port = 8001  # Must match the default WRITE_SERVICE_URL

    @mock_write_service_app.post("/query")
    async def mock_query(payload: Dict[str, str]):
        """
        Mock endpoint for the write_service's /query.
        Returns sample data for testing purposes.
        """
        # In a real mock, you might inspect payload['query'] to return different data
        # based on the SQL, but for this test, a fixed response is sufficient.
        mock_data = [
            {
                "mcp_name": "Sentinel-Core-MCP",
                "server_id": "srv-core-001",
                "risk_tier": "HIGH",
                "overall_risk": 0.98
            },
            {
                "mcp_name": "Data-Ingestion-MCP",
                "server_id": "srv-ingest-002",
                "risk_tier": "MEDIUM",
                "overall_risk": 0.55
            },
            {
                "mcp_name": "Reporting-MCP",
                "server_id": "srv-report-003",
                "risk_tier": "LOW",
                "overall_risk": 0.12
            }
        ]
        return mock_data

    # Function to run the mock write_service in a separate thread
    def run_mock_write_service():
        """Runs the mock FastAPI app using uvicorn."""
        uvicorn.run(
            mock_write_service_app,
            host="127.0.0.1",
            port=mock_write_service_port,
            log_level="warning",  # Suppress excessive uvicorn logs during test
            access_log=False
        )

    # Start the mock write_service in a daemon thread
    mock_thread = threading.Thread(target=run_mock_write_service, daemon=True)
    mock_thread.start()
    
    # Give the mock service a moment to fully start up
    time.sleep(1.5) 

    # Set the environment variable for the main app to point to the mock service
    # This ensures the `requests` call in `query_write_service` hits our mock.
    os.environ["WRITE_SERVICE_URL"] = f"http://127.0.0.1:{mock_write_service_port}"

    # Initialize TestClient for the main FastAPI application
    client = TestClient(app)

    try:
        print(f"Attempting to connect to mock write_service at {os.environ['WRITE_SERVICE_URL']}...")
        # Make a dummy request to the mock service to ensure it's up and responsive
        # This helps in debugging if the mock service fails to start.
        test_mock_response = requests.post(f"{os.environ['WRITE_SERVICE_URL']}/query", json={"query": "TEST"}, timeout=1)
        test_mock_response.raise_for_status()
        print("Mock write_service is responsive.")

        print("Making GET request to /mcps/risk_tiers...")
        response = client.get("/mcps/risk_tiers")
        response.raise_for_status()  # Ensure no HTTP errors from the main app

        data = response.json()

        # --- Acceptance Criteria Assertions ---
        # 1. Assert that the response is a non-empty list
        assert isinstance(data, list), f"Expected response to be a list, but got {type(data)}"
        assert len(data) > 0, "Expected a non-empty list of MCPs, but got an empty list"

        # 2. Assert that each item in the list has the required fields and valid types
        for i, item in enumerate(data):
            assert "mcp_name" in item, f"Item {i} missing 'mcp_name' field"
            assert isinstance(item["mcp_name"], str), f"Item {i}: 'mcp_name' should be a string, got {type(item['mcp_name'])}"
            assert item["mcp_name"], f"Item {i}: 'mcp_name' should not be empty"

            assert "server_id" in item, f"Item {i} missing 'server_id' field"
            assert isinstance(item["server_id"], str), f"Item {i}: 'server_id' should be a string, got {type(item['server_id'])}"
            assert item["server_id"], f"Item {i}: 'server_id' should not be empty"

            assert "risk_tier" in item, f"Item {i} missing 'risk_tier' field"
            assert isinstance(item["risk_tier"], str), f"Item {i}: 'risk_tier' should be a string, got {type(item['risk_tier'])}"
            assert item["risk_tier"] in ["LOW", "MEDIUM", "HIGH"], f"Item {i}: 'risk_tier' has unexpected value '{item['risk_tier']}'"

            assert "overall_risk" in item, f"Item {i} missing 'overall_risk' field"
            assert isinstance(item["overall_risk"], (int, float)), f"Item {i}: 'overall_risk' should be a number, got {type(item['overall_risk'])}"
            assert 0.0 <= item["overall_risk"] <= 1.0, f"Item {i}: 'overall_risk' ({item['overall_risk']}) should be between 0.0 and 1.0"

        print("PASS")

    except Exception as e:
        print(f"FAIL: {e}")
        if 'response' in locals():
            print(f"Response Status Code: {response.status_code}")
            print(f"Response Body: {response.text}")
    finally:
        # Clean up the environment variable set for the test
        if "WRITE_SERVICE_URL" in os.environ:
            del os.environ["WRITE_SERVICE_URL"]
        # The daemon thread for the mock service will automatically terminate
        # when the main program exits.