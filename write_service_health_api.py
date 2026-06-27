# write_service_health_api.py
from fastapi import APIRouter, HTTPException, Depends, FastAPI
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Dict, Any, Optional

# Initialize the FastAPI router
router = APIRouter()

# Pydantic model for the response of this API
class ServiceHealthResponse(BaseModel):
    last_heartbeat: datetime
    status: str
    meta: Dict[str, Any]

# Pydantic model for the internal health data returned by the 'write_service'
# This simulates the structure of an entry from the 'service_health' table
class WriteServiceInternalHealth(BaseModel):
    service: str
    last_heartbeat: datetime
    status: str
    meta: Dict[str, Any]

# A client class to simulate interaction with the 'write_service' HTTP endpoint
# In a real application, this would use an HTTP client like `httpx` or `requests`
class WriteServiceClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        # self.http_client = httpx.AsyncClient(base_url=base_url) # For actual network calls

    async def get_write_service_health(self) -> Optional[WriteServiceInternalHealth]:
        """
        Simulates fetching the health status of 'write_service' from its dedicated HTTP endpoint.
        In a real scenario, this would make an actual HTTP GET request.
        """
        # Example of a real HTTP call (commented out for no-network requirement):
        # try:
        #     response = await self.http_client.get("/health/write_service") # Assuming an endpoint like this
        #     response.raise_for_status()
        #     data = response.json()
        #     return WriteServiceInternalHealth(**data)
        # except httpx.HTTPStatusError as e:
        #     # Handle 4xx/5xx errors from the write_service
        #     print(f"Error fetching write service health: {e}")
        #     return None
        # except httpx.RequestError as e:
        #     # Handle network errors
        #     print(f"Network error fetching write service health: {e}")
        #     return None
        raise NotImplementedError("This method should be overridden by a mock or a real HTTP client implementation.")

# Dependency injector for the WriteServiceClient
async def get_write_service_client() -> WriteServiceClient:
    """
    Provides an instance of WriteServiceClient.
    In a production environment, the base_url would come from configuration.
    """
    # Using a dummy URL as it will be mocked in tests
    return WriteServiceClient(base_url="http://write-service-host:8001")

@router.get("/api/write_service_health", response_model=ServiceHealthResponse)
async def get_write_service_health_api(
    client: WriteServiceClient = Depends(get_write_service_client)
) -> ServiceHealthResponse:
    """
    Retrieves the health status of the 'write_service' by querying its dedicated
    HTTP endpoint.
    """
    health_data = await client.get_write_service_health()

    if health_data is None:
        # If the write_service's endpoint returns no data or an error,
        # it implies the entry is not found or the service is unavailable.
        raise HTTPException(
            status_code=404,
            detail="Write service health entry not found or unavailable."
        )

    # Ensure that the data returned is indeed for 'write_service'
    if health_data.service != "write_service":
        # This indicates a misconfiguration or unexpected behavior from the write_service
        raise HTTPException(
            status_code=500,
            detail="Unexpected service returned by write_service health endpoint."
        )

    return ServiceHealthResponse(
        last_heartbeat=health_data.last_heartbeat,
        status=health_data.status,
        meta=health_data.meta
    )

# Self-test block using FastAPI TestClient (no network)
if __name__ == "__main__":
    from fastapi.testclient import TestClient

    # Create a FastAPI app instance for testing
    app = FastAPI()
    app.include_router(router)

    # Mock client for testing without actual network calls
    class MockWriteServiceClient(WriteServiceClient):
        def __init__(self, mock_response: Optional[WriteServiceInternalHealth] = None):
            super().__init__(base_url="http://mock-url")
            self._mock_response = mock_response

        async def get_write_service_health(self) -> Optional[WriteServiceInternalHealth]:
            return self._mock_response

    # Override the dependency for testing purposes
    def override_get_write_service_client(mock_response: Optional[WriteServiceInternalHealth]):
        async def _override():
            return MockWriteServiceClient(mock_response)
        return _override

    print("Running self-tests for /api/write_service_health...")

    # Test Case 1: Write service health found and returned correctly
    test_datetime_1 = datetime.now(timezone.utc).replace(microsecond=0)
    mock_health_data_1 = WriteServiceInternalHealth(
        service="write_service",
        last_heartbeat=test_datetime_1,
        status="healthy",
        meta={"version": "1.0.0", "uptime_seconds": 3600}
    )
    app.dependency_overrides[get_write_service_client] = override_get_write_service_client(mock_health_data_1)

    client = TestClient(app)
    response = client.get("/api/write_service_health")

    assert response.status_code == 200, f"Test Case 1 Failed: Expected status 200, got {response.status_code}"
    data = response.json()
    assert "last_heartbeat" in data, "Test Case 1 Failed: 'last_heartbeat' key missing"
    assert "status" in data, "Test Case 1 Failed: 'status' key missing"
    assert "meta" in data, "Test Case 1 Failed: 'meta' key missing"
    assert data["status"] == "healthy", f"Test Case 1 Failed: Expected status 'healthy', got {data['status']}"
    assert data["meta"]["version"] == "1.0.0", f"Test Case 1 Failed: Expected meta version '1.0.0', got {data['meta']['version']}"
    assert data["last_heartbeat"] == test_datetime_1.isoformat(), f"Test Case 1 Failed: Datetime mismatch. Expected {test_datetime_1.isoformat()}, got {data['last_heartbeat']}"
    print("Test Case 1 (Health Found): PASS")

    # Test Case 2: Write service health entry not found (mock returns None)
    app.dependency_overrides[get_write_service_client] = override_get_write_service_client(None)
    response = client.get("/api/write_service_health")

    assert response.status_code == 404, f"Test Case 2 Failed: Expected status 404, got {response.status_code}"
    assert response.json()["detail"] == "Write service health entry not found or unavailable.", f"Test Case 2 Failed: Unexpected detail message: {response.json()['detail']}"
    print("Test Case 2 (Health Not Found): PASS")

    # Test Case 3: Write service returns data for a different service (should result in 500)
    test_datetime_3 = datetime.now(timezone.utc).replace(microsecond=0)
    mock_other_service_data = WriteServiceInternalHealth(
        service="other_service", # Intentionally wrong service name
        last_heartbeat=test_datetime_3,
        status="healthy",
        meta={"version": "2.0.0"}
    )
    app.dependency_overrides[get_write_service_client] = override_get_write_service_client(mock_other_service_data)
    response = client.get("/api/write_service_health")

    assert response.status_code == 500, f"Test Case 3 Failed: Expected status 500, got {response.status_code}"
    assert response.json()["detail"] == "Unexpected service returned by write_service health endpoint.", f"Test Case 3 Failed: Unexpected detail message: {response.json()['detail']}"
    print("Test Case 3 (Wrong Service Returned): PASS")

    print("All self-tests passed.")