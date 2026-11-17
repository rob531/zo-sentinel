"""Logic for threat-intel aggregator statistics."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import ThreatIntelRef


def _stale_threshold(db: Session) -> Optional[datetime]:
    """Return the timestamp used as the 24-hour freshness boundary."""
    newest = db.execute(select(func.max(ThreatIntelRef.fetched_at))).scalar()
    if newest is None:
        return None
    if isinstance(newest, datetime):
        return newest - timedelta(hours=24)
    return None


def get_aggregator_stats(db: Session) -> Dict[str, Any]:
    """Compute per-source and total threat-intel reference statistics."""
    threshold = _stale_threshold(db)

    stale_expression = (
        case((ThreatIntelRef.fetched_at < threshold, 1), else_=0)
        if threshold is not None
        else func.literal(0)
    )

    rows = db.execute(
        select(
            ThreatIntelRef.source,
            func.count(ThreatIntelRef.id),
            func.count(func.distinct(ThreatIntelRef.pulse_id)),
            func.count(func.distinct(ThreatIntelRef.indicator_type)),
            func.max(ThreatIntelRef.pulse_created),
            func.max(ThreatIntelRef.fetched_at),
            func.sum(case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)),
            func.sum(case((ThreatIntelRef.is_aggregator.is_(False), 1), else_=0)),
            func.sum(stale_expression),
        )
        .group_by(ThreatIntelRef.source)
        .order_by(ThreatIntelRef.source)
    ).all()

    sources: List[Dict[str, Any]] = []
    total_indicators = 0
    total_stale = 0

    for (
        source,
        total,
        pulses,
        indicator_types,
        latest_pulse_created,
        latest_fetched_at,
        aggregator_rows,
        non_aggregator_rows,
        stale_refs,
    ) in rows:
        total = int(total or 0)
        stale = int(stale_refs or 0)
        total_indicators += total
        total_stale += stale

        sources.append(
            {
                "source": source,
                "total": total,
                "pulses": int(pulses or 0),
                "indicator_types": int(indicator_types or 0),
                "latest_pulse_created": latest_pulse_created,
                "latest_fetched_at": latest_fetched_at,
                "aggregator_rows": int(aggregator_rows or 0),
                "non_aggregator_rows": int(non_aggregator_rows or 0),
                "stale_refs": stale,
            }
        )

    return {
        "sources": sources,
        "totals": {
            "sources": len(sources),
            "indicators": total_indicators,
            "stale": total_stale,
        },
    }


# Keep the common names available to thin routers and downstream callers.
aggregate_aggregator_stats = get_aggregator_stats
get_aggregator_statistics = get_aggregator_stats
compute_aggregator_stats = get_aggregator_stats


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    now = datetime.utcnow()
    old = now - timedelta(days=2)
    session.add_all(
        [
            ThreatIntelRef(
                id=1,
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-1",
                pulse_name="Pulse One",
                pulse_created=now,
                is_aggregator=True,
                source="alpha",
                source_url="https://example.test/alpha/1",
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id="pulse-1",
                pulse_name="Pulse One",
                pulse_created=now,
                is_aggregator=False,
                source="alpha",
                source_url="https://example.test/alpha/2",
                fetched_at=now - timedelta(minutes=30),
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="hash",
                indicator_value="abc123",
                pulse_id="pulse-2",
                pulse_name="Pulse Two",
                pulse_created=now - timedelta(hours=2),
                is_aggregator=False,
                source="alpha",
                source_url="https://example.test/alpha/3",
                fetched_at=old,
            ),
            ThreatIntelRef(
                id=4,
                indicator_type="ip",
                indicator_value="198.51.100.1",
                pulse_id="pulse-3",
                pulse_name="Pulse Three",
                pulse_created=now - timedelta(hours=1),
                is_aggregator=False,
                source="beta",
                source_url="https://example.test/beta/1",
                fetched_at=now - timedelta(hours=1),
            ),
        ]
    )
    session.commit()

    that_app = FastAPI()

    @that_app.get("/api/threat-intel/aggregator-stats")
    def aggregator_stats(db: Session = Depends(get_session)):
        return get_aggregator_stats(db)

    that_app.dependency_overrides[get_session] = lambda: session

    from fastapi.testclient import TestClient

    response = TestClient(that_app).get("/api/threat-intel/aggregator-stats")
    assert response.status_code == 200
    payload = response.json()
    alpha = next(item for item in payload["sources"] if item["source"] == "alpha")
    assert alpha["total"] == 3
    assert alpha["aggregator_rows"] == 1
    assert payload["totals"]["indicators"] == 4
    assert payload["totals"]["stale"] == 1
    print("PASS")