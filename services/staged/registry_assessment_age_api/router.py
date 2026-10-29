from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_assessment_age as _get_assessment_age


router = APIRouter(prefix="/api", tags=["registry"])


class AssessmentAgeBucket(BaseModel):
    bucket: str
    server_count: int
    share: float


class AssessmentAgeResponse(BaseModel):
    buckets: List[AssessmentAgeBucket]
    never_assessed: int
    stale_over_30d: int
    oldest_assessed_at: Optional[datetime]
    newest_assessed_at: Optional[datetime]
    total_servers: int
    generated_at: datetime


@router.get(
    "/registry/assessment-age",
    response_model=AssessmentAgeResponse,
    summary="Registry assessment age distribution",
)
def get_assessment_age(session=Depends(get_session)):
    return _get_assessment_age(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    test_rows = [
        McpServerRegistry(
            server_id="never-assessed",
            name="Never assessed",
            registry_source="test",
            trust_score=0.5,
            verdict="unknown",
            confidence=0.0,
            last_assessed=None,
            first_seen=now - timedelta(days=10),
            last_seen=now,
            last_scanned=now,
            scan_count=1,
            risk_tier="unknown",
        ),
        McpServerRegistry(
            server_id="assessed-yesterday",
            name="Assessed yesterday",
            registry_source="test",
            trust_score=0.8,
            verdict="trusted",
            confidence=0.9,
            last_assessed=now - timedelta(days=1),
            first_seen=now - timedelta(days=10),
            last_seen=now,
            last_scanned=now,
            scan_count=2,
            risk_tier="low",
        ),
        McpServerRegistry(
            server_id="assessed-ten-days",
            name="Assessed ten days ago",
            registry_source="test",
            trust_score=0.7,
            verdict="trusted",
            confidence=0.8,
            last_assessed=now - timedelta(days=10),
            first_seen=now - timedelta(days=20),
            last_seen=now,
            last_scanned=now,
            scan_count=3,
            risk_tier="medium",
        ),
        McpServerRegistry(
            server_id="assessed-100-days",
            name="Assessed one hundred days ago",
            registry_source="test",
            trust_score=0.2,
            verdict="untrusted",
            confidence=0.7,
            last_assessed=now - timedelta(days=100),
            first_seen=now - timedelta(days=200),
            last_seen=now,
            last_scanned=now,
            scan_count=4,
            risk_tier="high",
        ),
    ]

    with TestSession() as session:
        session.add_all(test_rows)
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/assessment-age")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_servers"] == 4, payload
        assert payload["never_assessed"] == 1, payload
        buckets = {item["bucket"]: item["server_count"] for item in payload["buckets"]}
        assert set(buckets) == {"never", "lt_1d", "1_7d", "7_30d", "gt_30d"}, payload
        assert buckets["gt_30d"] == 1, payload

    print("PASS")