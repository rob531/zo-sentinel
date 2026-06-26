from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
import requests
from datetime import datetime

router = APIRouter()

class HealthStatus(BaseModel):
    status: str
    count: int
    last_seen: str
    healthy: bool

def get_write_service() -> str:
    return "http://write_service:8772"

@router.get("/api/v1/mcp/submissions/health", response_model=HealthStatus)
async def get_health_status():
    write_service_url = get_write_service()
    try:
        response = requests.get(f"{write_service_url}/mcp_submissions")
        response.raise_for_status()
        data = response.json()

        if not data:
            return HealthStatus(
                status="No submissions found",
                count=0,
                last_seen="Never",
                healthy=False
            )

        last_submission = max(data, key=lambda x: x.get('timestamp', 0))
        last_seen = datetime.fromtimestamp(last_submission['timestamp']).isoformat()

        return HealthStatus(
            status="OK",
            count=len(data),
            last_seen=last_seen,
            healthy=True
        )
    except requests.RequestException as e:
        return HealthStatus(
            status=str(e),
            count=0,
            last_seen="Never",
            healthy=False
        )

if __name__ == "__main__":
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    response = client.get("/api/v1/mcp/submissions/health")
    assert response.status_code == 200
    assert response.json().get("healthy") is not None
    assert isinstance(response.json().get("count"), int)
    assert isinstance(response.json().get("last_seen"), str)
    assert isinstance(response.json().get("status"), str)