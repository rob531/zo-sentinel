"""Logic for the threat intelligence indicator lag service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import ThreatIntelRef


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_indicator_lag(db: Session, hours: int | None = None) -> List[Dict[str, Any]]:
    """Compute indicator-lag aggregates from the real threat intelligence table.

    ``hours`` is accepted for compatibility with the HTTP contract.  The
    reported lag is the age of the newest fetched row at computation time,
    as required by the service contract.
    """
    del hours

    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(
            ThreatIntelRef.indicator_type,
            func.count(ThreatIntelRef.id).label("ref_count"),
            func.count(func.distinct(ThreatIntelRef.pulse_id)).label("pulse_count"),
            func.sum(
                case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)
            ).label("aggregator_count"),
            func.min(ThreatIntelRef.fetched_at).label("oldest_fetched_at"),
            func.max(ThreatIntelRef.fetched_at).label("newest_fetched_at"),
        )
        .group_by(ThreatIntelRef.indicator_type)
    ).all()

    result: List[Dict[str, Any]] = []
    for row in rows:
        newest = _as_utc(row.newest_fetched_at)
        oldest = _as_utc(row.oldest_fetched_at)
        staleness_seconds = None
        if newest is not None:
            staleness_seconds = max(0, int((now - newest).total_seconds()))

        result.append(
            {
                "indicator_type": row.indicator_type,
                "ref_count": int(row.ref_count or 0),
                "pulse_count": int(row.pulse_count or 0),
                "aggregator_count": int(row.aggregator_count or 0),
                "oldest_fetched_at": oldest,
                "newest_fetched_at": newest,
                "staleness_seconds": staleness_seconds,
            }
        )

    result.sort(key=lambda item: (-item["ref_count"], item["indicator_type"] or ""))
    return result


# Compatibility names for routers using the service's common naming variants.
get_indicator_lag = compute_indicator_lag
summarize_indicator_lag = compute_indicator_lag


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    ThreatIntelRef.__table__.create(bind=engine)

    db = TestingSessionLocal()
    db.add_all(
        [
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="one.example",
                pulse_id="pulse-1",
                pulse_name="Pulse 1",
                is_aggregator=False,
                source="test",
                source_url="https://example.test/1",
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="two.example",
                pulse_id="pulse-2",
                pulse_name="Pulse 2",
                is_aggregator=True,
                source="test",
                source_url="https://example.test/2",
            ),
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-3",
                pulse_name="Pulse 3",
                is_aggregator=False,
                source="test",
                source_url="https://example.test/3",
            ),
        ]
    )
    db.commit()

    application = FastAPI()

    @application.get("/api/threat-intel/indicator-lag")
    def indicator_lag(session=__import__("fastapi").Depends(get_session)):
        return {
            "hours": 24,
            "generated_at": datetime.now(timezone.utc),
            "indicator_types": compute_indicator_lag(session),
        }

    application.dependency_overrides[get_session] = lambda: db
    response = TestClient(application).get("/api/threat-intel/indicator-lag?hours=24")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["indicator_types"]) == 2
    domain = next(
        item for item in payload["indicator_types"] if item["indicator_type"] == "domain"
    )
    assert domain["aggregator_count"] == 1
    assert domain["ref_count"] == 2
    print("PASS")
    db.close()
    engine.dispose()