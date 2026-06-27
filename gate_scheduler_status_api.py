import datetime
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Generator, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

# Initialize the FastAPI router
router = APIRouter()

# --- Database Dependency Interface ---
# In a real application, this would typically be a SQLAlchemy Session,
# a database connection pool, or an ORM object.
# For this self-contained example, we define a simple mock interface.

class MockDBSession:
    """
    A mock database session to simulate querying the 'service_health' table.
    It stores data in an in-memory dictionary.
    """
    def __init__(self, data: Dict[str, Dict[str, Any]]):
        # Expected data format:
        # {'daemon_name': {'last_heartbeat': datetime_obj}}
        self._data = data

    def get_service_health(self, daemon_name: str) -> Optional[Dict[str, Any]]:
        """
        Simulates fetching a row from the 'service_health' table for a given daemon.
        Conceptual SQL: SELECT last_heartbeat FROM service_health WHERE daemon_name = :daemon_name
        """
        return self._data.get(daemon_name)

# This function serves as a dependency injector for the database session.
# In a real application, it would establish a connection and yield a real session.
# For testing and this example, it will be overridden with a seeded mock session.
def get_db_session() -> Generator[MockDBSession, None, None]:
    """
    Dependency injector for the database session.
    Yields an empty mock session by default. This will be overridden in tests.
    """
    yield MockDBSession({}) # Default empty mock, to be overridden in __main__

# --- Endpoint Implementation ---

# Define the threshold for a heartbeat to be considered 'stale'
HEARTBEAT_STALE_THRESHOLD_SECONDS = 60

@router.get("/gate_scheduler/status", response_model=Dict[str, str])
async def get_gate_scheduler_status(
    db: MockDBSession = Depends(get_db_session)
) -> Dict[str, str]:
    """
    Returns the current status and last heartbeat timestamp for the 'gate_scheduler' daemon.
    The status is 'ok' if the last heartbeat was within the defined threshold,
    otherwise it's 'stale'.
    """
    daemon_name = "gate_scheduler"
    
    # Retrieve health data for the 'gate_scheduler' daemon
    health_data = db.get_service_health(daemon_name)

    last_heartbeat_dt: Optional[datetime] = None
    if health_data and 'last_heartbeat' in health_data:
        last_heartbeat_dt = health_data['last_heartbeat']

    current_time = datetime.now(timezone.utc)
    
    status: str
    last_heartbeat_iso: str

    if last_heartbeat_dt:
        # Ensure the stored timestamp is timezone-aware for accurate comparison.
        # If it's naive, assume UTC as is common for database storage.
        if last_heartbeat_dt.tzinfo is None:
            last_heartbeat_dt = last_heartbeat_dt.replace(tzinfo=timezone.utc)

        time_since_heartbeat = current_time - last_heartbeat_dt
        
        if time_since_heartbeat.total_seconds() < HEARTBEAT_STALE_THRESHOLD_SECONDS:
            status = "ok"
        else:
            status = "stale"
        
        # Format the timestamp to ISO 8601, including seconds precision
        last_heartbeat_iso = last_heartbeat_dt.isoformat(timespec='seconds')
    else:
        # If no heartbeat data is found for the daemon, it's considered stale.
        status = "stale"
        last_heartbeat_iso = "N/A" # Indicate that no timestamp is available

    return {"status": status, "last_heartbeat": last_heartbeat_iso}

# --- Self-test / __main__ block ---

if __name__ == "__main__":
    print("Running self-test for gate_scheduler_status_api.py...")

    # Create a FastAPI app instance and include the router
    app = FastAPI()
    app.include_router(router)

    # --- Test Case 1: Recent Heartbeat (Expected: 'ok') ---
    print("\n--- Test Case 1: Recent Heartbeat (Expected: 'ok') ---")
    
    # Seed the mock DB with a recent heartbeat (within the threshold)
    recent_heartbeat_time = datetime.now(timezone.utc) - timedelta(seconds=30)
    seeded_data_ok = {
        "gate_scheduler": {"last_heartbeat": recent_heartbeat_time}
    }
    mock_db_session_ok = MockDBSession(seeded_data_ok)

    # Override the `get_db_session` dependency for this test client
    app.dependency_overrides[get_db_session] = lambda: mock_db_session_ok

    client = TestClient(app)
    response = client.get("/gate_scheduler/status")

    assert response.status_code == 200, \
        f"Test Case 1 FAILED: Expected status code 200, got {response.status_code}"
    response_json = response.json()
    
    print(f"Response: {response_json}")
    assert response_json["status"] == "ok", \
        f"Test Case 1 FAILED: Expected status 'ok', got '{response_json['status']}'"
    assert "last_heartbeat" in response_json, \
        "Test Case 1 FAILED: Expected 'last_heartbeat' in response"
    
    # Validate ISO 8601 format
    try:
        datetime.fromisoformat(response_json["last_heartbeat"])
    except ValueError:
        assert False, \
            f"Test Case 1 FAILED: last_heartbeat '{response_json['last_heartbeat']}' is not a valid ISO 8601 timestamp"
    
    print("Test Case 1 PASSED")

    # --- Test Case 2: Stale Heartbeat (Expected: 'stale') ---
    print("\n--- Test Case 2: Stale Heartbeat (Expected: 'stale') ---")

    # Seed the mock DB with an old heartbeat (beyond the threshold)
    stale_heartbeat_time = datetime.now(timezone.utc) - timedelta(seconds=90)
    seeded_data_stale = {
        "gate_scheduler": {"last_heartbeat": stale_heartbeat_time}
    }
    mock_db_session_stale = MockDBSession(seeded_data_stale)

    # Override the dependency for this test client
    app.dependency_overrides[get_db_session] = lambda: mock_db_session_stale

    client = TestClient(app) # Re-initialize client to ensure dependency override applies
    response = client.get("/gate_scheduler/status")

    assert response.status_code == 200, \
        f"Test Case 2 FAILED: Expected status code 200, got {response.status_code}"
    response_json = response.json()
    
    print(f"Response: {response_json}")
    assert response_json["status"] == "stale", \
        f"Test Case 2 FAILED: Expected status 'stale', got '{response_json['status']}'"
    assert "last_heartbeat" in response_json, \
        "Test Case 2 FAILED: Expected 'last_heartbeat' in response"
    
    try:
        datetime.fromisoformat(response_json["last_heartbeat"])
    except ValueError:
        assert False, \
            f"Test Case 2 FAILED: last_heartbeat '{response_json['last_heartbeat']}' is not a valid ISO 8601 timestamp"
    
    print("Test Case 2 PASSED")

    # --- Test Case 3: Daemon Not Found (Expected: 'stale', 'N/A' heartbeat) ---
    print("\n--- Test Case 3: Daemon Not Found (Expected: 'stale', 'N/A') ---")

    # Seed the mock DB with no data for 'gate_scheduler'
    seeded_data_not_found = {}
    mock_db_session_not_found = MockDBSession(seeded_data_not_found)

    # Override the dependency for this test client
    app.dependency_overrides[get_db_session] = lambda: mock_db_session_not_found

    client = TestClient(app)
    response = client.get("/gate_scheduler/status")

    assert response.status_code == 200, \
        f"Test Case 3 FAILED: Expected status code 200, got {response.status_code}"
    response_json = response.json()
    
    print(f"Response: {response_json}")
    assert response_json["status"] == "stale", \
        f"Test Case 3 FAILED: Expected status 'stale', got '{response_json['status']}'"
    assert response_json["last_heartbeat"] == "N/A", \
        f"Test Case 3 FAILED: Expected 'last_heartbeat' 'N/A', got '{response_json['last_heartbeat']}'"
    
    print("Test Case 3 PASSED")

    print("\nAll self-tests PASSED.")