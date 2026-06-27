from datetime import datetime, timedelta
from typing import List, Optional
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.testclient import TestClient

app = FastAPI()

class DaemonStatus(BaseModel):
    name: str
    age: int
    status: str
    meta: Optional[dict] = None

class DaemonStatusResponse(BaseModel):
    daemons: List[DaemonStatus]

# Configurable thresholds for staleness (in seconds)
STALENESS_THRESHOLDS = {
    "gate_scheduler": 1800,  # 30 minutes
    "default": 300           # 5 minutes
}

def get_daemon_statuses() -> List[DaemonStatus]:
    try:
        # Query the write_service for service_health data
        response = requests.post(
            "http://localhost:8000/api/service_health",
            json={"query": "SELECT name, last_heartbeat, status, meta FROM service_health"}
        )
        response.raise_for_status()
        data = response.json()

        current_time = datetime.utcnow()
        daemons = []

        for item in data:
            name = item["name"]
            last_heartbeat = datetime.fromisoformat(item["last_heartbeat"])
            status = item["status"]
            meta = item.get("meta")

            # Calculate age in seconds
            age = (current_time - last_heartbeat).total_seconds()

            # Determine staleness threshold
            threshold = STALENESS_THRESHOLDS.get(name, STALENESS_THRESHOLDS["default"])

            # Determine if daemon is stale
            daemon_status = "stale" if age > threshold else "ok"

            daemons.append(DaemonStatus(
                name=name,
                age=int(age),
                status=daemon_status,
                meta=meta
            ))

        return daemons

    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error querying write_service: {str(e)}")

@app.get("/api/stale_daemons_status", response_model=DaemonStatusResponse)
async def get_stale_daemons_status():
    daemons = get_daemon_statuses()
    return {"daemons": daemons}

if __name__ == "__main__":
    # Mock write_service response for testing
    class MockResponse:
        def __init__(self, json_data, status_code):
            self.json_data = json_data
            self.status_code = status_code

        def json(self):
            return self.json_data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.RequestException("Mock request failed")

    def mock_post(*args, **kwargs):
        # Mock response with some test data
        mock_data = [
            {
                "name": "write_service",
                "last_heartbeat": (datetime.utcnow() - timedelta(minutes=10)).isoformat(),
                "status": "ok",
                "meta": {"version": "1.0"}
            },
            {
                "name": "gate_scheduler",
                "last_heartbeat": (datetime.utcnow() - timedelta(minutes=40)).isoformat(),
                "status": "ok",
                "meta": {"version": "2.0"}
            },
            {
                "name": "other_service",
                "last_heartbeat": (datetime.utcnow() - timedelta(minutes=2)).isoformat(),
                "status": "ok",
                "meta": {"version": "1.1"}
            }
        ]
        return MockResponse(mock_data, 200)

    # Patch requests.post to use our mock
    requests.post = mock_post

    # Create test client
    client = TestClient(app)

    # Test the API
    response = client.get("/api/stale_daemons_status")
    assert response.status_code == 200

    # Verify response structure
    data = response.json()
    assert isinstance(data, dict)
    assert "daemons" in data
    assert isinstance(data["daemons"], list)

    # Verify at least one daemon is marked as stale
    stale_daemons = [d for d in data["daemons"] if d["status"] == "stale"]
    assert len(stale_daemons) > 0

    # Verify write_service is stale
    write_service = next((d for d in data["daemons"] if d["name"] == "write_service"), None)
    assert write_service is not None
    assert write_service["status"] == "stale"

    print("PASS")