from fastapi import APIRouter, HTTPException
from fastapi.testclient import TestClient
import requests
from typing import List, Dict, Any
from datetime import datetime, timedelta

router = APIRouter()

class ServiceHealth:
    def __init__(self, name: str, last_heartbeat: datetime, status: str):
        self.name = name
        self.last_heartbeat = last_heartbeat
        self.status = status

def get_service_health(service_name: str) -> ServiceHealth:
    # Query the service_health table for the given service
    # This is a placeholder for the actual database query
    # In a real application, you would use a database client to query the table
    # For the sake of this example, we'll simulate a database query
    if service_name == "write_service":
        return ServiceHealth("write_service", datetime.now(), "healthy")
    elif service_name == "mcp_scanner":
        return ServiceHealth("mcp_scanner", datetime.now() - timedelta(minutes=5), "healthy")
    elif service_name == "trust_synthesiser":
        return ServiceHealth("trust_synthesiser", datetime.now() - timedelta(minutes=10), "degraded")
    elif service_name == "inference_router":
        return ServiceHealth("inference_router", datetime.now() - timedelta(minutes=15), "unhealthy")
    else:
        raise ValueError(f"Unknown service: {service_name}")

def query_write_service_for_health(service_name: str) -> ServiceHealth:
    # Use requests.post to query the write_service for database access
    # This is a placeholder for the actual request
    # In a real application, you would use the appropriate URL and headers
    # For the sake of this example, we'll simulate a request
    response = requests.post("http://write_service/health", json={"service_name": service_name})
    if response.status_code == 200:
        data = response.json()
        return ServiceHealth(data["name"], datetime.fromisoformat(data["last_heartbeat"]), data["status"])
    else:
        raise HTTPException(status_code=response.status_code, detail="Failed to query write_service")

@router.get("/health")
async def health_check() -> Dict[str, Any]:
    critical_services = ["write_service", "mcp_scanner", "trust_synthesiser", "inference_router"]
    service_statuses = []

    for service_name in critical_services:
        try:
            service_health = query_write_service_for_health(service_name)
            service_statuses.append({
                "name": service_health.name,
                "last_heartbeat": service_health.last_heartbeat.isoformat(),
                "status": service_health.status
            })
        except Exception as e:
            service_statuses.append({
                "name": service_name,
                "last_heartbeat": None,
                "status": "unhealthy",
                "error": str(e)
            })

    overall_status = "healthy"
    for status in service_statuses:
        if status["status"] == "unhealthy":
            overall_status = "unhealthy"
            break
        elif status["status"] == "degraded" and overall_status != "unhealthy":
            overall_status = "degraded"

    return {
        "overall_status": overall_status,
        "service_statuses": service_statuses
    }

if __name__ == "__main__":
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "overall_status" in data
    assert "service_statuses" in data
    assert len(data["service_statuses"]) >= 3

    # Check that the overall_status reflects the health of the services
    service_statuses = [status["status"] for status in data["service_statuses"]]
    if "unhealthy" in service_statuses:
        assert data["overall_status"] == "unhealthy"
    elif "degraded" in service_statuses:
        assert data["overall_status"] == "degraded"
    else:
        assert data["overall_status"] == "healthy"

    print("All tests passed!")