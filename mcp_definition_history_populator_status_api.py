# mcp_definition_history_populator_status_api.py

from fastapi import FastAPI, Depends
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

# --- Pydantic Models ---

class McpDefinitionHistoryPopulatorStatus(BaseModel):
    """
    Represents the current status of the MCP Definition History Populator Daemon.
    """
    is_running: bool
    last_heartbeat: Optional[datetime] = None
    last_successful_run: Optional[datetime] = None
    last_error: Optional[str] = None
    records_processed: Optional[int] = None

# --- Simulated Write Service (Dependency) ---
# In a real application, this would be an actual service interacting with the database.
# For this module, we simulate it as per the directive:
# "No direct database writes or network calls are allowed from this module itself,
# beyond querying the local `write_service`."

class WriteService:
    """
    A simulated service to fetch data from 'service_health' and
    'mcp_definition_history_metrics' tables.
    """
    def get_service_health_status(self, service_name: str) -> Optional[dict]:
        """
        Simulates querying the 'service_health' table for a given service.
        Returns a dictionary with status information or None if not found.
        """
        # In a real scenario, this would query the database.
        # Example data for demonstration:
        if service_name == "mcp_definition_history_populator_daemon":
            return {
                "is_running": True,
                "last_heartbeat": datetime.now(),
                "last_error": None, # Or "Some error message"
            }
        return None

    def get_mcp_definition_history_metrics(self) -> Optional[dict]:
        """
        Simulates querying the 'mcp_definition_history_metrics' table.
        Returns a dictionary with metrics or None if the table/metrics are not available.
        """
        # In a real scenario, this would query the database.
        # Example data for demonstration:
        return {
            "last_successful_run": datetime.now(),
            "records_processed": 12345,
        }
        # Return None if the table doesn't exist or no metrics are found
        # return None

# --- FastAPI Application ---

app = FastAPI(
    title="MCP Definition History Populator Status API",
    description="API to provide status of the mcp_definition_history_populator_daemon.",
    version="1.0.0",
)

# Dependency injector for WriteService
def get_write_service() -> WriteService:
    """
    Provides an instance of the WriteService.
    This allows for easy mocking in tests.
    """
    return WriteService()

# --- API Endpoint ---

@app.get(
    "/mcp-definition-history-populator/status",
    response_model=McpDefinitionHistoryPopulatorStatus,
    summary="Get MCP Definition History Populator Daemon Status",
    description="Returns the current status, heartbeat, last successful run, "
                "errors, and processed records for the populator daemon.",
)
async def get_populator_status(
    write_service: WriteService = Depends(get_write_service)
) -> McpDefinitionHistoryPopulatorStatus:
    """
    Retrieves the status of the `mcp_definition_history_populator_daemon`.

    Queries the `service_health` table for general daemon status
    and the `mcp_definition_history_metrics` table for populator-specific metrics.
    """
    daemon_name = "mcp_definition_history_populator_daemon"

    # Fetch general daemon health status
    health_status = write_service.get_service_health_status(daemon_name)
    is_running = health_status.get("is_running", False) if health_status else False
    last_heartbeat = health_status.get("last_heartbeat") if health_status else None
    last_error = health_status.get("last_error") if health_status else None

    # Fetch populator-specific metrics
    metrics = write_service.get_mcp_definition_history_metrics()
    last_successful_run = metrics.get("last_successful_run") if metrics else None
    records_processed = metrics.get("records_processed") if metrics else None

    return McpDefinitionHistoryPopulatorStatus(
        is_running=is_running,
        last_heartbeat=last_heartbeat,
        last_successful_run=last_successful_run,
        last_error=last_error,
        records_processed=records_processed,
    )

# --- Acceptance Test (using TestClient) ---

if __name__ == "__main__":
    from fastapi.testclient import TestClient

    print("Running acceptance tests for mcp_definition_history_populator_status_api.py...")

    # Define a MockWriteService for testing purposes
    class MockWriteService:
        def get_service_health_status(self, service_name: str) -> Optional[dict]:
            if service_name == "mcp_definition_history_populator_daemon":
                return {
                    "is_running": True,
                    "last_heartbeat": datetime(2023, 1, 15, 10, 30, 0),
                    "last_error": None,
                }
            return None

        def get_mcp_definition_history_metrics(self) -> Optional[dict]:
            return {
                "last_successful_run": datetime(2023, 1, 15, 10, 25, 0),
                "records_processed": 98765,
            }

    # Override the dependency for the test client
    app.dependency_overrides[get_write_service] = MockWriteService

    client = TestClient(app)

    # Make the GET request to the endpoint
    response = client.get("/mcp-definition-history-populator/status")

    # --- Assertions ---

    # 1. Assert status code is 200 OK
    assert response.status_code == 200, \
        f"FAIL: Expected status code 200, but got {response.status_code}"

    # 2. Assert valid JSON response
    try:
        data = response.json()
    except Exception as e:
        assert False, f"FAIL: Response is not valid JSON: {e}"

    assert isinstance(data, dict), "FAIL: Response JSON is not a dictionary"

    # 3. Assert expected status fields
    expected_fields = [
        "is_running",
        "last_heartbeat",
        "last_successful_run",
        "last_error",
        "records_processed",
    ]
    for field in expected_fields:
        assert field in data, f"FAIL: Missing expected field: '{field}' in response"

    # Assert types and values from mock data
    assert isinstance(data["is_running"], bool), \
        f"FAIL: 'is_running' type mismatch, expected bool, got {type(data['is_running'])}"
    assert data["is_running"] is True, \
        f"FAIL: 'is_running' value mismatch, expected True, got {data['is_running']}"

    assert data["last_heartbeat"] == "2023-01-15T10:30:00", \
        f"FAIL: 'last_heartbeat' value mismatch, expected '2023-01-15T10:30:00', got {data['last_heartbeat']}"

    assert data["last_successful_run"] == "2023-01-15T10:25:00", \
        f"FAIL: 'last_successful_run' value mismatch, expected '2023-01-15T10:25:00', got {data['last_successful_run']}"

    assert data["last_error"] is None, \
        f"FAIL: 'last_error' value mismatch, expected None, got {data['last_error']}"

    assert isinstance(data["records_processed"], int), \
        f"FAIL: 'records_processed' type mismatch, expected int, got {type(data['records_processed'])}"
    assert data["records_processed"] == 98765, \
        f"FAIL: 'records_processed' value mismatch, expected 98765, got {data['records_processed']}"

    print("PASS")

    # Optional: Test case for when metrics might be missing (e.g., table not yet populated)
    class MockWriteServiceNoMetrics(MockWriteService):
        def get_mcp_definition_history_metrics(self) -> Optional[dict]:
            return None # Simulate no metrics available

    app.dependency_overrides[get_write_service] = MockWriteServiceNoMetrics
    response_no_metrics = client.get("/mcp-definition-history-populator/status")
    data_no_metrics = response_no_metrics.json()

    assert response_no_metrics.status_code == 200
    assert data_no_metrics["last_successful_run"] is None
    assert data_no_metrics["records_processed"] is None
    print("PASS (No metrics scenario)")

    # Clean up dependency override
    app.dependency_overrides = {}