from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import List, Optional
import httpx
from app.db import get_session
from app.models import ServiceHealth
from sqlalchemy.orm import Session

router = APIRouter()

class DaemonStatus(BaseModel):
    service: str
    status: str
    last_heartbeat: datetime
    seconds_since_heartbeat: int
    is_stale: bool

class HealthSummary(BaseModel):
    total: int
    healthy: int
    stale: int
    critical_stale: int

class HealthResponse(BaseModel):
    daemons: List[DaemonStatus]
    summary: HealthSummary
    generated_at: datetime

def get_write_service_client():
    return httpx.AsyncClient(base_url="http://127.0.0.1:8772")

def calculate_status(heartbeat: datetime) -> str:
    now = datetime.utcnow()
    delta = (now - heartbeat).total_seconds()
    if delta > 3600:
        return "critical"
    elif delta > 300:
        return "stale"
    return "healthy"

async def fetch_service_health() -> List[ServiceHealth]:
    async with get_write_service_client() as client:
        response = await client.post("/query", json={
            "query": "SELECT * FROM service_health"
        })
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch service health")
        return response.json()

@router.get("/health/sentinel", response_model=HealthResponse)
async def get_sentinel_health():
    health_data = await fetch_service_health()

    daemons = []
    summary = {
        "total": 0,
        "healthy": 0,
        "stale": 0,
        "critical_stale": 0
    }

    now = datetime.utcnow()
    for daemon in health_data:
        last_heartbeat = datetime.fromisoformat(daemon["last_heartbeat"])
        delta = (now - last_heartbeat).total_seconds()
        status = calculate_status(last_heartbeat)
        is_stale = delta > 300

        daemons.append({
            "service": daemon["service"],
            "status": status,
            "last_heartbeat": last_heartbeat,
            "seconds_since_heartbeat": int(delta),
            "is_stale": is_stale
        })

        summary["total"] += 1
        if status == "healthy":
            summary["healthy"] += 1
        elif status == "stale":
            summary["stale"] += 1
        elif status == "critical":
            summary["critical_stale"] += 1

    return {
        "daemons": daemons,
        "summary": summary,
        "generated_at": now
    }

@router.get("/health/daemons", response_model=HealthResponse)
async def get_daemons_health():
    return await get_sentinel_health()

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    with TestSession() as session:
        session.add_all([
            ServiceHealth(
                service="daemon1",
                status="healthy",
                last_heartbeat=datetime.utcnow() - timedelta(seconds=100)
            ),
            ServiceHealth(
                service="daemon2",
                status="healthy",
                last_heartbeat=datetime.utcnow() - timedelta(seconds=400)
            ),
            ServiceHealth(
                service="daemon3",
                status="healthy",
                last_heartbeat=datetime.utcnow() - timedelta(seconds=4000)
            )
        ])
        session.commit()

    client = TestClient(app)

    # Test /health/sentinel
    response = client.get("/health/sentinel")
    assert response.status_code == 200
    data = response.json()

    # Check at least one daemon with correct stale flagging
    has_stale = any(daemon["is_stale"] for daemon in data["daemons"])
    assert has_stale, "No stale daemons found in test data"

    print("PASS")