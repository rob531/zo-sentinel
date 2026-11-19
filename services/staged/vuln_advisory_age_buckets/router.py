"""router.py -- vulnerability advisory age and severity bucket API."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_advisory_age_buckets
except ImportError:
    from .logic import advisory_age_buckets as get_advisory_age_buckets


router = APIRouter(prefix="/api/vuln", tags=["vuln"])


class SeverityBucket(BaseModel):
    severity: Literal["critical", "high", "medium", "low"]
    advisories: int
    linked_servers: int
    median_age_days: float
    max_age_days: float
    median_fetch_lag_days: float


class AdvisoryTotals(BaseModel):
    advisories: int
    linked_servers: int


class AdvisoryAgeResponse(BaseModel):
    days: int | None = None
    generated_at: datetime
    buckets: list[SeverityBucket]
    totals: AdvisoryTotals


@router.get("/advisory-age", response_model=AdvisoryAgeResponse)
def get_advisory_age(
    severity: Literal["critical", "high", "medium", "low"] | None = Query(
        default=None
    ),
    days: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_session),
) -> AdvisoryAgeResponse:
    result = get_advisory_age_buckets(db, severity=severity, days=days)

    if isinstance(result, dict):
        payload = dict(result)
    elif isinstance(result, tuple) and len(result) == 2:
        payload = {
            "buckets": result[0],
            "totals": result[1],
        }
    else:
        payload = {"buckets": result}

    payload.setdefault("days", days)
    payload.setdefault("generated_at", datetime.now(timezone.utc))
    return AdvisoryAgeResponse.model_validate(payload)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import VulnAdvisory, VulnLink

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)

    now = datetime.now(timezone.utc)
    advisories = [
        VulnAdvisory(
            id=1,
            feed="test-feed",
            severity="critical",
            ecosystem="npm",
            package="critical-package",
            published_at=now - timedelta(days=10),
            fetched_at=now - timedelta(days=9),
            content_hash="critical-hash-1",
            source_url="https://example.invalid/critical-1",
        ),
        VulnAdvisory(
            id=2,
            feed="test-feed",
            severity="critical",
            ecosystem="npm",
            package="critical-package-2",
            published_at=now - timedelta(days=20),
            fetched_at=now - timedelta(days=19),
            content_hash="critical-hash-2",
            source_url="https://example.invalid/critical-2",
        ),
        VulnAdvisory(
            id=3,
            feed="test-feed",
            severity="medium",
            ecosystem="npm",
            package="medium-package",
            published_at=now - timedelta(days=15),
            fetched_at=now - timedelta(days=14),
            content_hash="medium-hash-1",
            source_url="https://example.invalid/medium-1",
        ),
        VulnAdvisory(
            id=4,
            feed="test-feed",
            severity="medium",
            ecosystem="npm",
            package="medium-package-2",
            published_at=now - timedelta(days=25),
            fetched_at=now - timedelta(days=24),
            content_hash="medium-hash-2",
            source_url="https://example.invalid/medium-2",
        ),
    ]
    links = [
        VulnLink(
            id=1,
            advisory_id=1,
            server_id="server-1",
            match_basis="package",
            match_confidence=1.0,
            match_value="critical-package",
            linked_at=now,
        ),
        VulnLink(
            id=2,
            advisory_id=3,
            server_id="server-2",
            match_basis="package",
            match_confidence=1.0,
            match_value="medium-package",
            linked_at=now,
        ),
    ]

    with TestingSession() as session:
        session.add_all(advisories)
        session.add_all(links)
        session.commit()

        test_app = FastAPI()
        test_app.include_router(router)

        def override_get_session():
            with TestingSession() as session:
                yield session

        test_app.dependency_overrides[get_session] = override_get_session
        client = TestClient(test_app)
        response = client.get("/api/vuln/advisory-age")
        assert response.status_code == 200, response.text
        body = response.json()
        assert any(bucket["severity"] == "critical" for bucket in body["buckets"])
        critical = next(
            bucket for bucket in body["buckets"] if bucket["severity"] == "critical"
        )
        assert critical["advisories"] == 2
        assert critical["linked_servers"] == 1
        assert body["totals"]["advisories"] == 4

    print("PASS")