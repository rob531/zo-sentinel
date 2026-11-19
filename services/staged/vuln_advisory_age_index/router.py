"""HTTP surface for the vulnerability advisory age index service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

try:
    from .logic import get_advisory_age_index as _advisory_age_index
except ImportError:  # pragma: no cover - supports the alternate logic export name
    from .logic import advisory_age_index as _advisory_age_index

router = APIRouter(prefix="/api", tags=["vuln-advisory-age"])


class AdvisoryAgeParams(BaseModel):
    ecosystem: str | None = None
    severity: str | None = None
    max_age_days: int = 90


class AdvisoryAgeBucket(BaseModel):
    bucket: str
    count: int


class OldestAdvisory(BaseModel):
    id: int | str
    package: str | None = None
    severity: str | None = None
    ecosystem: str | None = None
    age_days: int
    source_url: str


class AdvisoryAgeResponse(BaseModel):
    params: AdvisoryAgeParams
    buckets: list[AdvisoryAgeBucket]
    oldest: list[OldestAdvisory]
    total: int
    generated_at: str


@router.get("/vuln/advisory-age", response_model=AdvisoryAgeResponse)
def get_advisory_age(
    ecosystem: str | None = None,
    severity: str | None = None,
    max_age_days: int = 90,
    db: Session = Depends(get_session),
) -> AdvisoryAgeResponse:
    result: dict[str, Any] = _advisory_age_index(
        db,
        ecosystem=ecosystem,
        severity=severity,
        max_age_days=max_age_days,
    )
    return AdvisoryAgeResponse(**result)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    advisory_rows = [
        VulnAdvisory(
            id=1,
            feed="test-feed",
            source_url="https://example.invalid/advisories/1",
            package="package-a",
            severity="high",
            ecosystem="npm",
            published_at=now - timedelta(days=5),
            fetched_at=now,
        ),
        VulnAdvisory(
            id=2,
            feed="test-feed",
            source_url="https://example.invalid/advisories/2",
            package="package-b",
            severity="low",
            ecosystem="npm",
            published_at=now - timedelta(days=45),
            fetched_at=now,
        ),
        VulnAdvisory(
            id=3,
            feed="test-feed",
            source_url="https://example.invalid/advisories/3",
            package="package-c",
            severity="high",
            ecosystem="pypi",
            published_at=now - timedelta(days=120),
            fetched_at=now,
        ),
        VulnAdvisory(
            id=4,
            feed="test-feed",
            source_url="https://example.invalid/advisories/4",
            package="package-d",
            severity="low",
            ecosystem="pypi",
            published_at=now - timedelta(days=300),
            fetched_at=now,
        ),
        VulnAdvisory(
            id=5,
            feed="test-feed",
            source_url="https://example.invalid/advisories/5",
            package="package-e",
            severity="high",
            ecosystem="npm",
            published_at=now - timedelta(days=500),
            fetched_at=now,
        ),
    ]
    with TestSession() as session:
        session.add_all(advisory_rows)
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    response = client.get("/api/vuln/advisory-age")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert next(b for b in payload["buckets"] if b["bucket"] == "0-30")["count"] == 1
    assert payload["oldest"][0]["age_days"] >= 500

    filtered = client.get(
        "/api/vuln/advisory-age",
        params={"severity": "high"},
    )
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert filtered_payload["total"] == 3
    assert all(row["severity"] == "high" for row in filtered_payload["oldest"])

    print("PASS")