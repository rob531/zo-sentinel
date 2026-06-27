import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.testclient import TestClient

# --- Pydantic Models ---
class SelfDiagnosticsStatusResponse(BaseModel):
    """
    Pydantic model for the response of the self-diagnostics status API.
    """
    status: str
    last_heartbeat: datetime.datetime
    meta: Optional[Dict[str, Any]] = None

# --- FastAPI Router Setup ---
router = APIRouter()

# --- Placeholder for write_service ---
# In a real application, this would typically be an imported function
# or a dependency injected via FastAPI's Depends system,
# responsible for executing database queries.
async def write_service(query: str, params: Dict[str, Any] = None) -> list[Dict[str, Any]]:
    """
    Placeholder for the database service responsible for executing queries.
    This function must be implemented or mocked for actual database interaction.
    """
    # This NotImplementedError will be caught and converted to a 500 error
    # if write_service is not properly mocked or implemented.
    raise NotImplementedError("write_service must be implemented or mocked for actual use.")

# --- API Endpoint ---
@router.get("/self_diagnostics/status", response_model=SelfDiagnosticsStatusResponse)
async def get_self_diagnostics_status():
    """
    Retrieves the latest health status and diagnostic reports for the
    'self_diagnostics' daemon from the 'service_health' table.
    """
    query = """
        SELECT status, last_heartbeat, meta
        FROM service_health
        WHERE daemon_name = :daemon_name
        ORDER BY last_heartbeat DESC
        LIMIT 1;
    """
    params = {"daemon_name": "self_diagnostics"}

    try:
        result = await write_service(query, params)
    except NotImplementedError:
        # Catch the placeholder error and provide a more user-friendly message
        raise HTTPException(status_code=500, detail="Database service is not configured.")
    except Exception as e:
        # Catch any other potential database query errors
        raise HTTPException(status_code=500, detail=f"Failed to retrieve self-diagnostics status: {e}")

    if not result:
        # If no entry is found for 'self_diagnostics'
        raise HTTPException(status_code=404, detail="Self-diagnostics status not found.")

    latest_status = result[0]

    # Return the data conforming to the SelfDiagnosticsStatusResponse model
    return SelfDiagnosticsStatusResponse(
        status=latest_status["status"],
        last_heartbeat=latest_status["last_heartbeat"],
        meta=latest_status.get("meta") # .get() to handle cases where 'meta' might be null
    )

# --- Acceptance Test Block ---
if __name__ == "__main__":
    app = FastAPI()
    app.include_router(router)

    # --- Mock write_service for testing ---
    # In a real test suite, you might use unittest.mock.patch or a dependency override.
    # For this self-contained __main__ block, we'll directly replace the function.
    import sys
    current_module = sys.modules[__name__]

    async def mock_write_service(query: str, params: Dict[str, Any] = None) -> list[Dict[str, Any]]:
        """
        Mock implementation of write_service to simulate database responses
        for the acceptance test.
        """
        if params and params.get("daemon_name") == "self_diagnostics":
            # Seed data for the 'self_diagnostics' daemon
            seeded_heartbeat = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
            return [
                {
                    "status": "healthy",
                    "last_heartbeat": seeded_heartbeat,
                    "meta": {"version": "1.0.0", "uptime_seconds": 300, "last_check": "2023-10-27T10:00:00Z"}
                }
            ]
        return [] # Return empty list for other daemon_names or if no match

    # Replace the actual write_service with our mock for the test
    current_module.write_service = mock_write_service

    client = TestClient(app)

    print("Running acceptance test for /self_diagnostics/status...")

    # Make a request to the API endpoint
    response = client.get("/self_diagnostics/status")

    # --- Assertions ---
    assert response.status_code == 200, f"Expected status code 200, but got {response.status_code}. Response: {response.text}"
    
    data = response.json()

    # Assert required fields are present
    assert "status" in data, "Response JSON is missing 'status' field."
    assert "last_heartbeat" in data, "Response JSON is missing 'last_heartbeat' field."
    assert "meta" in data, "Response JSON is missing 'meta' field."

    # Assert field values
    assert data["status"] == "healthy", f"Expected status 'healthy', but got '{data['status']}'."
    
    # Assert last_heartbeat is a valid ISO 8601 datetime string
    try:
        parsed_heartbeat = datetime.datetime.fromisoformat(data["last_heartbeat"])
        assert isinstance(parsed_heartbeat, datetime.datetime), "last_heartbeat is not a valid datetime object."
    except ValueError:
        assert False, f"last_heartbeat '{data['last_heartbeat']}' is not a valid ISO 8601 datetime string."

    expected_meta = {"version": "1.0.0", "uptime_seconds": 300, "last_check": "2023-10-27T10:00:00Z"}
    assert data["meta"] == expected_meta, f"Expected meta {expected_meta}, but got {data['meta']}."

    print("PASS: /self_diagnostics/status returned a valid JSON response with expected fields.")

    # Optionally, restore the original write_service if this were part of a larger application
    # current_module.write_service = _original_write_service