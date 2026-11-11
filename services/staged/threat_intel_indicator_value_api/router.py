from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

from .logic import get_indicator_values

router = APIRouter()


@router.get("/api/threat-intel/indicator-value")
def threat_intel_indicator_value(
    indicator_type: str | None = Query(default=None),
    limit: int = Query(default=25),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    bounded_limit = max(1, min(limit, 100))
    result = get_indicator_values(session, indicator_type, bounded_limit)

    if isinstance(result, dict):
        items = result.get("items", [])
        aggregate = result.get("aggregate", {})
    else:
        items, aggregate = result

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "indicator_type": indicator_type,
        "items": items,
        "aggregate": aggregate,
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(test_engine)
    test_session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    seed_rows = [
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            pulse_created=now,
            is_aggregator=False,
            source="test",
            source_url="https://example.test/1",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="pulse-1",
            pulse_name="Pulse One",
            pulse_created=now,
            is_aggregator=True,
            source="test",
            source_url="https://example.test/2",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="pulse-2",
            pulse_name="Pulse Two",
            pulse_created=now,
            is_aggregator=False,
            source="test",
            source_url="https://example.test/3",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="domain",
            indicator_value="example.org",
            pulse_id="pulse-2",
            pulse_name="Pulse Two",
            pulse_created=now,
            is_aggregator=True,
            source="test",
            source_url="https://example.test/4",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=5,
            indicator_type="ip",
            indicator_value="192.0.2.3",
            pulse_id="pulse-2",
            pulse_name="Pulse Two",
            pulse_created=now,
            is_aggregator=False,
            source="test",
            source_url="https://example.test/5",
            fetched_at=now,
        ),
    ]

    with test_session_factory() as session:
        session.add_all(seed_rows)
        session.commit()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/threat-intel/indicator-value",
            params={"indicator_type": "ip", "limit": 2},
        )
        response.raise_for_status()
        payload = response.json()
        assert len(payload["items"]) <= 2

    print("PASS")