from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef

try:
    from .logic import get_freshness_stats
except ImportError:
    from .logic import get_ref_freshness as get_freshness_stats

router = APIRouter(prefix="/api", tags=["threat-intel"])


class SourceFreshness(BaseModel):
    source: str
    refs: int
    stale_refs: int
    newest_fetched_at: Optional[datetime] = None
    pulse_count: int


class RefFreshnessResponse(BaseModel):
    max_age_hours: int
    total_refs: int
    distinct_indicator_types: int
    distinct_sources: int
    stale_refs: int
    stale_pct: float
    newest_fetched_at: Optional[datetime] = None
    sources: List[SourceFreshness]


@router.get("/threat-intel/ref-freshness", response_model=RefFreshnessResponse)
def get_ref_freshness(
    max_age_hours: int = Query(48, ge=0),
    session=Depends(get_session),
):
    return get_freshness_stats(session, max_age_hours=max_age_hours)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    TestSession = sessionmaker(bind=engine)

    now = datetime.utcnow()
    old = now - timedelta(hours=72)

    with TestSession() as db:
        db.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="Pulse 1",
                    pulse_created=now - timedelta(hours=2),
                    is_aggregator=False,
                    source="abuse.ch",
                    source_url="https://abuse.ch/example/1",
                    fetched_at=now - timedelta(hours=1),
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.org",
                    pulse_id="pulse-2",
                    pulse_name="Pulse 2",
                    pulse_created=now - timedelta(hours=3),
                    is_aggregator=False,
                    source="abuse.ch",
                    source_url="https://abuse.ch/example/2",
                    fetched_at=old,
                ),
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value="198.51.100.1",
                    pulse_id="pulse-3",
                    pulse_name="Pulse 3",
                    pulse_created=now - timedelta(hours=1),
                    is_aggregator=False,
                    source="otx",
                    source_url="https://otx.example/3",
                    fetched_at=now - timedelta(minutes=30),
                ),
            ]
        )
        db.commit()

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/threat-intel/ref-freshness",
            params={"max_age_hours": 48},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_refs"] == 3, payload
        assert payload["distinct_sources"] == 2, payload
        assert payload["stale_refs"] >= 1, payload
        assert payload["stale_pct"] > 0, payload
        assert len(payload["sources"]) == 2, payload
        assert sum(source["refs"] for source in payload["sources"]) == 3, payload

    print("PASS")