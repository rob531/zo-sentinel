"""router.py -- registry assessment staleness HTTP surface.

Exposes the service router and delegates all registry analysis to the
service's logic layer.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import assessment_staleness as _assessment_staleness
except ImportError:  # pragma: no cover - compatibility with older staged logic
    try:
        from .logic import get_assessment_staleness as _assessment_staleness
    except ImportError:  # pragma: no cover
        from .logic import compute_assessment_staleness as _assessment_staleness


router = APIRouter(
    prefix="/api/registry/assessment-staleness",
    tags=["registry"],
)


class OverallStaleness(BaseModel):
    model_config = ConfigDict(extra="ignore")

    count: int
    stale: int
    never_assessed: int
    median_hours: float | None = None
    p95_hours: float | None = None


class TierStaleness(BaseModel):
    model_config = ConfigDict(extra="ignore")

    risk_tier: str
    count: int
    stale: int
    never_assessed: int
    median_hours: float | None = None


class StalestServer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    server_id: str
    name: str | None = None
    risk_tier: str | None = None


class AssessmentStalenessResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bucket_hours: int
    overall: OverallStaleness
    by_tier: list[TierStaleness]
    stalest: list[StalestServer]


@router.get("", response_model=AssessmentStalenessResponse)
def get_assessment_staleness(
    bucket_hours: int = Query(24, gt=0),
    db: Session = Depends(get_session),
) -> AssessmentStalenessResponse:
    result = _assessment_staleness(db, bucket_hours)
    return AssessmentStalenessResponse.model_validate(result)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    McpServerRegistry.__table__.create(engine)

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="stale-server",
                    name="Stale Server",
                    risk_tier="high",
                    last_assessed=now - timedelta(days=365),
                    last_seen=now,
                    first_seen=now - timedelta(days=365),
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="never-assessed-server",
                    name="Never Assessed Server",
                    risk_tier="medium",
                    last_assessed=None,
                    last_seen=now,
                    first_seen=now - timedelta(days=30),
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="recent-server",
                    name="Recent Server",
                    risk_tier="low",
                    last_assessed=now - timedelta(hours=1),
                    last_seen=now,
                    first_seen=now - timedelta(days=2),
                    scan_count=2,
                ),
            ]
        )
        session.commit()

    response = TestClient(test_app).get(
        "/api/registry/assessment-staleness",
        params={"bucket_hours": 24},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["overall"]["count"] == 3
    assert payload["overall"]["never_assessed"] == 1
    assert payload["stalest"]
    print("PASS")