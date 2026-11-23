"""HTTP surface for threat intelligence staleness auditing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import get_staleness_audit

router = APIRouter(prefix="/api", tags=["threat-intel"])


class StalenessBucket(BaseModel):
    bucket: str
    count: int


class SourceStaleness(BaseModel):
    source: str
    count: int
    oldest_fetched_at: datetime | None = None
    newest_fetched_at: datetime | None = None
    inconsistent_count: int = 0


class StalenessResponse(BaseModel):
    total_rows: int
    buckets: list[StalenessBucket]
    by_source: list[SourceStaleness]
    generated_at: datetime


@router.get("/threat-intel/staleness", response_model=StalenessResponse)
def get_threat_intel_staleness(
    db: Session = Depends(get_session),
) -> StalenessResponse:
    payload = get_staleness_audit(db)
    return StalenessResponse(
        total_rows=payload["total_rows"],
        buckets=payload["buckets"],
        by_source=payload["by_source"],
        generated_at=payload["generated_at"],
    )


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
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-1",
            pulse_name="pulse-one",
            pulse_created=now - timedelta(days=2),
            is_aggregator=False,
            source="alpha",
            source_url="https://example.invalid/alpha",
            fetched_at=now - timedelta(hours=1),
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.invalid",
            pulse_id="pulse-2",
            pulse_name="pulse-two",
            pulse_created=now - timedelta(days=5),
            is_aggregator=False,
            source="alpha",
            source_url="https://example.invalid/alpha-2",
            fetched_at=now - timedelta(days=2),
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="pulse-3",
            pulse_name="pulse-three",
            pulse_created=now - timedelta(days=5),
            is_aggregator=False,
            source="beta",
            source_url="https://example.invalid/beta",
            fetched_at=now - timedelta(days=4),
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.net",
            pulse_id="pulse-4",
            pulse_name="pulse-four",
            pulse_created=now - timedelta(days=12),
            is_aggregator=False,
            source="beta",
            source_url="https://example.invalid/beta-2",
            fetched_at=now - timedelta(days=10),
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.3",
            pulse_id="pulse-5",
            pulse_name="pulse-five",
            pulse_created=now - timedelta(days=40),
            is_aggregator=False,
            source="gamma",
            source_url="https://example.invalid/gamma",
            fetched_at=now - timedelta(days=40),
        ),
    ]
    rows[0].pulse_created = rows[0].fetched_at + timedelta(hours=1)

    with TestSession() as session:
        session.add_all(rows)
        session.commit()

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/staleness")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total_rows"] == 5
        assert sum(item["count"] for item in body["buckets"]) == 5
        assert any(
            source["source"] == "alpha" and source["inconsistent_count"] >= 1
            for source in body["by_source"]
        )

    print("PASS")