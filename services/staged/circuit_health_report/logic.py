from datetime import datetime
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session

router = APIRouter()


class BreakerInfo(BaseModel):
    tripped: bool
    since: Optional[datetime]


class ServiceInfo(BaseModel):
    name: str
    age_seconds: int
    status: str


class RetryBudgetInfo(BaseModel):
    file: str
    attempts: int
    max_attempts: int
    last_error: Optional[str]


class QuarantinedInfo(BaseModel):
    file: str
    quarantined_at: datetime
    reason: Optional[str]


class CircuitHealthResponse(BaseModel):
    breaker: BreakerInfo
    services: List[ServiceInfo]
    retry_budget: List[RetryBudgetInfo]
    quarantined: List[QuarantinedInfo]


async def _fetch_circuit_health() -> dict:
    """Query the write‑service for the current circuit health state."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://127.0.0.1:8772/query",
            json={"table": "service_health"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()


@router.get("/api/circuit/health", response_model=CircuitHealthResponse)
async def circuit_health(session=Depends(get_session)):
    raw = await _fetch_circuit_health()
    # Pydantic will coerce the raw dict into the response model.
    return raw


if __name__ == "__main__":
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    # Mock the external call for the self‑test.
    async def _mock_fetch_circuit_health() -> dict:
        return {
            "breaker": {"tripped": True, "since": "2023-01-01T00:00:00Z"},
            "services": [
                {"name": "svc1", "age_seconds": 120, "status": "ok"},
                {"name": "svc2", "age_seconds": 300, "status": "degraded"},
            ],
            "retry_budget": [
                {
                    "file": "file1.py",
                    "attempts": 3,
                    "max_attempts": 5,
                    "last_error": "Timeout",
                },
                {
                    "file": "file2.py",
                    "attempts": 1,
                    "max_attempts": 5,
                    "last_error": None,
                },
            ],
            "quarantined": [
                {
                    "file": "retention_sweeper.py",
                    "quarantined_at": "2023-01-02T12:00:00Z",
                    "reason": "Error",
                },
                {
                    "file": "other.py",
                    "quarantined_at": "2023-01-03T12:00:00Z",
                    "reason": "Fail",
                },
                {
                    "file": "third.py",
                    "quarantined_at": "2023-01-04T12:00:00Z",
                    "reason": "Bug",
                },
            ],
        }

    # Override the fetch function used by the endpoint.
    globals()["_fetch_circuit_health"] = _mock_fetch_circuit_health

    client = TestClient(app)

    resp = client.get("/api/circuit/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["breaker"]["tripped"] is True
    assert len(data["retry_budget"]) == 2
    assert data["quarantined"][0]["file"] == "retention_sweeper.py"
    print("PASS")