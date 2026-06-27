from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import datetime

app = FastAPI()

# Mock database models
class ServiceHealth(BaseModel):
    service_name: str
    status: str
    last_heartbeat: datetime.datetime
    meta: Dict[str, Any]

class WriteServiceMetrics(BaseModel):
    avg_latency: float
    error_rate: float
    request_count: int

# Mock database
mock_db = {
    "service_health": [
        ServiceHealth(
            service_name="write_service",
            status="healthy",
            last_heartbeat=datetime.datetime.now(),
            meta={"version": "1.0.0", "environment": "production"}
        )
    ],
    "write_service_metrics": [
        WriteServiceMetrics(
            avg_latency=0.15,
            error_rate=0.01,
            request_count=1000
        )
    ]
}

# Mock query functions
def query_service_health(service_name: str) -> Optional[ServiceHealth]:
    for health in mock_db["service_health"]:
        if health.service_name == service_name:
            return health
    return None

def query_write_service_metrics() -> Optional[WriteServiceMetrics]:
    if mock_db["write_service_metrics"]:
        return mock_db["write_service_metrics"][0]
    return None

@app.get("/write_service/summary")
async def get_write_service_summary():
    # Query service health
    service_health = query_service_health("write_service")
    if not service_health:
        raise HTTPException(status_code=404, detail="Write service health data not found")

    # Query metrics
    metrics = query_write_service_metrics()

    # Prepare response
    response = {
        "status": service_health.status,
        "last_heartbeat": service_health.last_heartbeat.isoformat(),
        "meta": service_health.meta,
    }

    if metrics:
        response.update({
            "avg_latency": metrics.avg_latency,
            "error_rate": metrics.error_rate,
            "request_count": metrics.request_count
        })

    return response

if __name__ == "__main__":
    from fastapi.testclient import TestClient

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/write_service/summary")
    assert response.status_code == 200
    data = response.json()

    # Verify the response structure and content
    assert "status" in data
    assert "last_heartbeat" in data
    assert "meta" in data
    assert "avg_latency" in data
    assert "error_rate" in data
    assert "request_count" in data

    print("PASS")