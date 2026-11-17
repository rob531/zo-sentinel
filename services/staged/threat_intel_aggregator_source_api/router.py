from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

try:
    from .logic import get_aggregator_stats
except ImportError:
    from .logic import get_aggregator_summary as get_aggregator_stats


router = APIRouter(prefix="/api", tags=["threat-intel"])


class AggregatorResponse(BaseModel):
    source: Optional[str]
    days: int
    total: int
    by_indicator_type: Dict[str, int]
    age_buckets: Dict[str, int]
    newest_fetched_at: Optional[datetime]
    oldest_fetched_at: Optional[datetime]


@router.get(
    "/threat-intel/aggregators",
    response_model=AggregatorResponse,
    summary="Get threat intelligence aggregator statistics",
)
def get_aggregators(
    source: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> AggregatorResponse:
    result = get_aggregator_stats(db, source=source, days=days)

    if hasattr(result, "model_dump"):
        result = result.model_dump()
    elif hasattr(result, "dict"):
        result = result.dict()

    return AggregatorResponse(
        source=result.get("source", source),
        days=result.get("days", days),
        total=result.get("total", 0),
        by_indicator_type=result.get("by_indicator_type", {}),
        age_buckets=result.get("age_buckets", {}),
        newest_fetched_at=result.get("newest_fetched_at"),
        oldest_fetched_at=result.get("oldest_fetched_at"),
    )


if __name__ == "__main__":
    from datetime import timedelta, timezone

    from fastapi import FastAPI
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

    now = datetime.now(timezone.utc)
    seed_rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="p1",
            pulse_name="Pulse 1",
            pulse_created=now - timedelta(days=2),
            is_aggregator=True,
            source="alpha",
            source_url="https://example.test/1",
            fetched_at=now - timedelta(hours=2),
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="p1",
            pulse_name="Pulse 1",
            pulse_created=now - timedelta(days=2),
            is_aggregator=True,
            source="alpha",
            source_url="https://example.test/2",
            fetched_at=now - timedelta(days=2),
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="p2",
            pulse_name="Pulse 2",
            pulse_created=now - timedelta(days=4),
            is_aggregator=True,
            source="alpha",
            source_url="https://example.test/3",
            fetched_at=now - timedelta(days=5),
        ),
        ThreatIntelRef(
            indicator_type="hash",
            indicator_value="a" * 64,
            pulse_id="p2",
            pulse_name="Pulse 2",
            pulse_created=now - timedelta(days=8),
            is_aggregator=True,
            source="alpha",
            source_url="https://example.test/4",
            fetched_at=now - timedelta(days=10),
        ),
        ThreatIntelRef(
            indicator_type="url",
            indicator_value="https://example.test/a",
            pulse_id="p3",
            pulse_name="Pulse 3",
            pulse_created=now - timedelta(days=40),
            is_aggregator=True,
            source="alpha",
            source_url="https://example.test/5",
            fetched_at=now - timedelta(days=40),
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.3",
            pulse_id="p3",
            pulse_name="Pulse 3",
            pulse_created=now - timedelta(days=1),
            is_aggregator=False,
            source="alpha",
            source_url="https://example.test/6",
            fetched_at=now - timedelta(hours=1),
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="beta.test",
            pulse_id="p4",
            pulse_name="Pulse 4",
            pulse_created=now - timedelta(days=3),
            is_aggregator=True,
            source="beta",
            source_url="https://example.test/7",
            fetched_at=now - timedelta(days=3),
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.4",
            pulse_id="p4",
            pulse_name="Pulse 4",
            pulse_created=now - timedelta(days=5),
            is_aggregator=True,
            source="beta",
            source_url="https://example.test/8",
            fetched_at=now - timedelta(days=6),
        ),
        ThreatIntelRef(
            indicator_type="url",
            indicator_value="https://beta.test/a",
            pulse_id="p5",
            pulse_name="Pulse 5",
            pulse_created=now - timedelta(days=10),
            is_aggregator=True,
            source="beta",
            source_url="https://example.test/9",
            fetched_at=now - timedelta(days=12),
        ),
        ThreatIntelRef(
            indicator_type="hash",
            indicator_value="b" * 64,
            pulse_id="p5",
            pulse_name="Pulse 5",
            pulse_created=now - timedelta(days=20),
            is_aggregator=False,
            source="beta",
            source_url="https://example.test/10",
            fetched_at=now - timedelta(days=20),
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.5",
            pulse_id="p6",
            pulse_name="Pulse 6",
            pulse_created=now - timedelta(days=15),
            is_aggregator=True,
            source="gamma",
            source_url="https://example.test/11",
            fetched_at=now - timedelta(days=15),
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="gamma.test",
            pulse_id="p6",
            pulse_name="Pulse 6",
            pulse_created=now - timedelta(days=16),
            is_aggregator=False,
            source="gamma",
            source_url="https://example.test/12",
            fetched_at=now - timedelta(days=16),
        ),
    ]

    test_db = TestSession()
    test_db.add_all(seed_rows)
    test_db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_db

    with TestClient(test_app) as client:
        response = client.get(
            "/api/threat-intel/aggregators",
            params={"source": "alpha", "days": 30},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 5, payload
        assert sum(payload["by_indicator_type"].values()) == payload["total"], payload
        assert set(payload["age_buckets"]) == {
            "0-1d",
            "1-7d",
            "7-30d",
            "30d+",
        }, payload

    test_db.close()
    test_engine.dispose()
    print("PASS")