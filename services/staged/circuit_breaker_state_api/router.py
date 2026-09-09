from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import requests

router = APIRouter()


class QuarantinedFile(BaseModel):
    file: str
    quarantined_at: str
    consecutive_fails: int
    last_error: str


class CircuitBreakerStateResponse(BaseModel):
    breaker_state: str
    trip_reason: Optional[str]
    trip_timestamp: Optional[str]
    quarantine_count: int
    quarantined_files: list[QuarantinedFile]


def get_circuit_breaker_state() -> CircuitBreakerStateResponse:
    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "table": "service_health",
            "columns": ["breaker_tripped", "trip_reason", "trip_timestamp", "quarantine_list"],
            "limit": 1
        },
        timeout=5
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return CircuitBreakerStateResponse(
            breaker_state="ok",
            trip_reason=None,
            trip_timestamp=None,
            quarantine_count=0,
            quarantined_files=[]
        )
    row = rows[0]
    breaker_tripped = row.get("breaker_tripped", False)
    quarantine_list = row.get("quarantine_list") or []
    return CircuitBreakerStateResponse(
        breaker_state="tripped" if breaker_tripped else "ok",
        trip_reason=row.get("trip_reason"),
        trip_timestamp=row.get("trip_timestamp"),
        quarantine_count=len(quarantine_list),
        quarantined_files=[
            QuarantinedFile(
                file=q.get("file", ""),
                quarantined_at=q.get("quarantined_at", ""),
                consecutive_fails=q.get("consecutive_fails", 0),
                last_error=q.get("last_error", "")
            )
            for q in quarantine_list
        ]
    )


@router.get("/diagnostics/circuit-breaker", response_model=CircuitBreakerStateResponse)
async def get_breaker_state():
    return get_circuit_breaker_state()


if __name__ == "__main__":
    from unittest.mock import patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    mock_health_state = {
        "breaker_tripped": True,
        "trip_reason": "Exceeded consecutive fail threshold",
        "trip_timestamp": "2024-01-15T10:30:00Z",
        "quarantine_list": [
            {"file": "agent_outputs/suspicious.py", "quarantined_at": "2024-01-15T10:30:00Z", "consecutive_fails": 5, "last_error": "Timeout"},
            {"file": "agent_outputs/malformed.json", "quarantined_at": "2024-01-15T10:29:00Z", "consecutive_fails": 3, "last_error": "Parse error"},
            {"file": "agent_outputs/failed.bin", "quarantined_at": "2024-01-15T10:28:00Z", "consecutive_fails": 2, "last_error": "CRC mismatch"}
        ]
    }

    app = FastAPI()
    app.include_router(router, prefix="/api")

    class MockAppState:
        circuit_breaker = mock_health_state

    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = [mock_health_state]
        mock_post.return_value.raise_for_status = lambda: None

        with TestClient(app) as client:
            response = client.get("/api/diagnostics/circuit-breaker")
            data = response.json()

    assert data["breaker_state"] == "tripped", f"Expected tripped, got {data['breaker_state']}"
    assert data["quarantine_count"] == 3, f"Expected 3, got {data['quarantine_count']}"
    file_names = [q["file"] for q in data["quarantined_files"]]
    assert "agent_outputs/suspicious.py" in file_names, f"Missing known file in {file_names}"

    print("PASS")