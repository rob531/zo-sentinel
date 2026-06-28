from fastapi import APIRouter, HTTPException
from fastapi.testclient import TestClient
from typing import List, Dict, Any
import requests

router = APIRouter()

@router.get("/daemon_heartbeats/detail", response_model=List[Dict[str, Any]])
def get_daemon_heartbeats_detail():
    query = """
    SELECT name, last_heartbeat, status, meta
    FROM service_health
    """
    try:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": query})
        response.raise_for_status()
        data = response.json()
        if not data:
            return []
        return data
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi import FastAPI
    import uvicorn

    app = FastAPI()
    app.include_router(router)

    # Mock the write_service responses
    def mock_query(request):
        if request.json["query"] == """
    SELECT name, last_heartbeat, status, meta
    FROM service_health
    """:
            return {
                "json": [
                    {"name": "daemon1", "last_heartbeat": "2023-01-01T00:00:00", "status": "healthy", "meta": {"key": "value"}},
                    {"name": "daemon2", "last_heartbeat": None, "status": "unhealthy", "meta": None},
                ]
            }
        return {"json": []}

    requests.post = mock_query

    client = TestClient(app)

    response = client.get("/daemon_heartbeats/detail")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["name"] == "daemon1"
    assert data[0]["last_heartbeat"] == "2023-01-01T00:00:00"
    assert data[0]["status"] == "healthy"
    assert data[0]["meta"] == {"key": "value"}
    assert data[1]["name"] == "daemon2"
    assert data[1]["last_heartbeat"] is None
    assert data[1]["status"] == "unhealthy"
    assert data[1]["meta"] is None

    print("PASS")