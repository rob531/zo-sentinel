from datetime import datetime
from typing import Optional

import requests
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/internal")


class WriteServiceHealthResponse(BaseModel):
    healthy: bool
    status: Optional[str] = None
    last_heartbeat: Optional[str] = None
    age_seconds: Optional[float] = None


@router.get("/write_service_health", response_model=WriteServiceHealthResponse)
def get_write_service_health() -> WriteServiceHealthResponse:
    sql = (
        "SELECT status, last_heartbeat FROM service_health "
        "WHERE service='write_service' ORDER BY last_heartbeat DESC LIMIT 1"
    )
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query", json={"query": sql}, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        if rows:
            row = rows[0]
            status = row.get("status")
            last = row.get("last_heartbeat")
        else:
            status = None
            last = None
    except Exception:
        status = None
        last = None

    age_seconds: Optional[float] = None
    if last:
        try:
            dt = datetime.fromisoformat(last)
            age_seconds = (datetime.utcnow() - dt).total_seconds()
        except Exception:
            age_seconds = None

    healthy = (
        status == "running"
        and age_seconds is not None
        and age_seconds <= 60
    )

    return WriteServiceHealthResponse(
        healthy=healthy,
        status=status,
        last_heartbeat=last,
        age_seconds=age_seconds,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from unittest.mock import patch
    from datetime import timedelta

    app = FastAPI()
    app.include_router(router)

    now = datetime.utcnow()
    mock_status = "running"
    mock_last = (now - timedelta(seconds=30)).isoformat()
    mock_response_data = {"rows": [{"status": mock_status, "last_heartbeat": mock_last}]}

    class MockResponse:
        def __init__(self, json_data):
            self._json = json_data
            self.status_code = 200

        def json(self):
            return self._json

        def raise_for_status(self):
            pass

    def mock_post(url, json, timeout):
        assert url == "http://127.0.0.1:8772/query"
        return MockResponse(mock_response_data)

    with patch("requests.post", side_effect=mock_post):
        client = TestClient(app)
        resp = client.get("/api/internal/write_service_health")
        assert resp.status_code == 200
        data = resp.json()
        expected_age = (datetime.utcnow() - datetime.fromisoformat(mock_last)).total_seconds()
        assert abs(data["age_seconds"] - expected_age) < 1.0
        assert data["healthy"] is True
        print("PASS")