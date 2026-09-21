from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
import requests

router = APIRouter()


class QuarantinedFile(BaseModel):
    file: str
    quarantined_at: str
    consecutive_fails: int
    last_error: str


class CircuitBreakerResponse(BaseModel):
    breaker_state: str
    trip_reason: Optional[str]
    trip_timestamp: Optional[str]
    quarantine_count: int
    quarantined_files: List[QuarantinedFile]


@router.get("/diagnostics/circuit-breaker", response_model=CircuitBreakerResponse)
async def get_circuit_breaker_status():
    health_state = _fetch_health_state()
    breaker_state = health_state.get("status", "ok")
    quarantine = health_state.get("quarantine", [])
    quarantined_files = [
        QuarantinedFile(
            file=q["file"],
            quarantined_at=q["quarantined_at"],
            consecutive_fails=q["consecutive_fails"],
            last_error=q["last_error"]
        )
        for q in quarantine
    ]
    return CircuitBreakerResponse(
        breaker_state=breaker_state,
        trip_reason=health_state.get("trip_reason") if breaker_state == "tripped" else None,
        trip_timestamp=health_state.get("trip_timestamp") if breaker_state == "tripped" else None,
        quarantine_count=len(quarantined_files),
        quarantined_files=quarantined_files
    )


def _fetch_health_state():
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"table": "service_health", "filter": {"service": "gate_8"}},
            timeout=5
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return {}


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from unittest.mock import patch

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)

    mock_health_state = {
        "status": "tripped",
        "trip_reason": "excessive_failures",
        "trip_timestamp": "2024-01-15T10:30:00Z",
        "quarantine": [
            {"file": "axis_score_audit.py", "quarantined_at": "2024-01-15T10:30:00Z", "consecutive_fails": 5, "last_error": "timeout"},
            {"file": "gate_health_check.py", "quarantined_at": "2024-01-15T10:25:00Z", "consecutive_fails": 3, "last_error": "connection_refused"},
            {"file": "server_registry_sync.py", "quarantined_at": "2024-01-15T10:20:00Z", "consecutive_fails": 2, "last_error": "validation_error"}
        ]
    }

    def mock_post(url, **kwargs):
        class MockResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return mock_health_state
        return MockResponse()

    app.dependency_overrides[get_session] = override_get_session

    with patch("requests.post", mock_post):
        with TestClient(app) as client:
            response = client.get("/diagnostics/circuit-breaker")
            assert response.status_code == 200
            data = response.json()
            assert data["breaker_state"] == "tripped"
            assert data["quarantine_count"] == 3
            assert data["quarantined_files"][0]["file"] == "axis_score_audit.py"

    print("PASS")