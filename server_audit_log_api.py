from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel
import requests
from typing import List
from fastapi.testclient import TestClient

app = FastAPI()
router = APIRouter()

class AuditLogEntry(BaseModel):
    timestamp: str
    target_server_id: str

@router.get("/servers/{target_server_id}/audit_log", response_model=List[AuditLogEntry])
async def get_audit_log(target_server_id: str):
    try:
        query = f"SELECT timestamp, target_server_id FROM audit_log WHERE target_server_id = '{target_server_id}'"
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": query}
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

app.include_router(router)

if __name__ == "__main__":
    # Mock write_service response
    def mock_post(url, json):
        if json["query"] == "SELECT timestamp, target_server_id FROM audit_log WHERE target_server_id = 'test_server'":
            return MockResponse([{"timestamp": "2023-01-01T00:00:00Z", "target_server_id": "test_server"}])
        return MockResponse([])

    class MockResponse:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

        def raise_for_status(self):
            pass

    requests.post = mock_post

    client = TestClient(app)
    response = client.get("/servers/test_server/audit_log")
    assert response.status_code == 200
    assert response.json() == [{"timestamp": "2023-01-01T00:00:00Z", "target_server_id": "test_server"}]
    print("PASS")