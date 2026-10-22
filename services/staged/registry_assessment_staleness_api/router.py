from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import AssessmentStalenessResponse, get_assessment_staleness

router = APIRouter(prefix="/api", tags=["registry"])


@router.get(
    "/registry/assessment-staleness",
    response_model=AssessmentStalenessResponse,
)
def assessment_staleness(
    session: Session = Depends(get_session),
) -> AssessmentStalenessResponse:
    return get_assessment_staleness(session)


if __name__ == "__main__":
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    fixtures = [
        {
            "server_id": "never-assessed",
            "name": "Never Assessed",
            "risk_tier": "critical",
            "verdict": "unknown",
            "last_assessed": None,
            "first_seen": now - timedelta(days=100),
            "last_seen": now - timedelta(days=1),
            "scan_count": 2,
            "registry_source": "github",
        },
        {
            "server_id": "fresh-1",
            "name": "Fresh One",
            "risk_tier": "low",
            "verdict": "approved",
            "last_assessed": now - timedelta(hours=6),
            "first_seen": now - timedelta(days=30),
            "last_seen": now,
            "scan_count": 5,
            "registry_source": "manual",
        },
        {
            "server_id": "stale-1",
            "name": "Stale One",
            "risk_tier": "high",
            "verdict": "review",
            "last_assessed": now - timedelta(days=10),
            "first_seen": now - timedelta(days=80),
            "last_seen": now - timedelta(days=2),
            "scan_count": 4,
            "registry_source": "github",
        },
        {
            "server_id": "old-1",
            "name": "Old One",
            "risk_tier": "critical",
            "verdict": "blocked",
            "last_assessed": now - timedelta(days=60),
            "first_seen": now - timedelta(days=200),
            "last_seen": now - timedelta(days=5),
            "scan_count": 8,
            "registry_source": "manual",
        },
        {
            "server_id": "fresh-2",
            "name": "Fresh Two",
            "risk_tier": "medium",
            "verdict": "approved",
            "last_assessed": now - timedelta(hours=18),
            "first_seen": now - timedelta(days=20),
            "last_seen": now,
            "scan_count": 3,
            "registry_source": "aws",
        },
        {
            "server_id": "old-2",
            "name": "Old Two",
            "risk_tier": "low",
            "verdict": "deprecated",
            "last_assessed": now - timedelta(days=120),
            "first_seen": now - timedelta(days=300),
            "last_seen": now - timedelta(days=10),
            "scan_count": 12,
            "registry_source": "github",
        },
    ]

    with TestingSessionLocal() as db:
        for fixture in fixtures:
            db.add(McpServerRegistry(**fixture))
        db.commit()

    def override_get_session() -> Any:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(local_app).get("/api/registry/assessment-staleness")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_servers"] == 6
    assert payload["never_assessed"] == 1
    assert all(
        key in payload
        for key in (
            "generated_at",
            "total_servers",
            "never_assessed",
            "buckets",
            "oldest",
            "median_age_days",
        )
    )
    stale_buckets = [
        bucket for bucket in payload["buckets"] if bucket["bucket"] == "7-30d"
    ]
    assert stale_buckets and stale_buckets[0]["count"] >= 1
    print("PASS")