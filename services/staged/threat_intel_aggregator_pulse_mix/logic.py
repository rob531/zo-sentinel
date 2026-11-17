"""Logic for threat intelligence pulse mix aggregation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _cutoff(days: int = 7) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def get_pulse_mix(db: Session, limit: int = 50) -> Dict[str, Any]:
    """Return pulse mix statistics grouped by indicator type and source."""
    total = int(db.execute(select(func.count()).select_from(ThreatIntelRef)).scalar() or 0)

    indicator_rows = db.execute(
        select(
            ThreatIntelRef.indicator_type,
            func.count(),
            func.count(func.distinct(ThreatIntelRef.pulse_id)),
        )
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(func.count().desc(), ThreatIntelRef.indicator_type.asc())
    ).all()

    source_rows = db.execute(
        select(
            ThreatIntelRef.source,
            func.count(),
            func.max(ThreatIntelRef.pulse_created),
        )
        .group_by(ThreatIntelRef.source)
        .order_by(func.count().desc(), ThreatIntelRef.source.asc())
    ).all()

    aggregator_count = int(
        db.execute(
            select(func.count())
            .select_from(ThreatIntelRef)
            .where(ThreatIntelRef.is_aggregator.is_(True))
        ).scalar()
        or 0
    )
    stale_count = int(
        db.execute(
            select(func.count())
            .select_from(ThreatIntelRef)
            .where(ThreatIntelRef.fetched_at < _cutoff())
        ).scalar()
        or 0
    )

    bounded_limit = max(0, int(limit))
    by_indicator_type: List[Dict[str, Any]] = [
        {
            "indicator_type": indicator_type,
            "count": int(count),
            "distinct_pulses": int(distinct_pulses),
        }
        for indicator_type, count, distinct_pulses in indicator_rows[:bounded_limit]
    ]
    by_source: List[Dict[str, Any]] = [
        {
            "source": source,
            "count": int(count),
            "latest_pulse_at": latest_pulse_at,
        }
        for source, count, latest_pulse_at in source_rows[:bounded_limit]
    ]

    return {
        "total": total,
        "by_indicator_type": by_indicator_type,
        "by_source": by_source,
        "aggregator_ratio": (aggregator_count / total) if total else 0.0,
        "stale_count": stale_count,
    }


def pulse_mix_summary(db: Session, limit: int = 50) -> Dict[str, Any]:
    """Compatibility alias for callers using the shorter summary name."""
    return get_pulse_mix(db, limit=limit)


def _self_test() -> None:
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    ThreatIntelRef.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()

    now = datetime.now(timezone.utc)
    rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-1",
            pulse_name="Pulse 1",
            pulse_created=now,
            is_aggregator=True,
            source="source-a",
            source_url="https://example.test/1",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="pulse-2",
            pulse_name="Pulse 2",
            pulse_created=now,
            is_aggregator=False,
            source="source-a",
            source_url="https://example.test/2",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="pulse-3",
            pulse_name="Pulse 3",
            pulse_created=now,
            is_aggregator=False,
            source="source-b",
            source_url="https://example.test/3",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.test/path",
            pulse_id="pulse-4",
            pulse_name="Pulse 4",
            pulse_created=now,
            is_aggregator=False,
            source="source-b",
            source_url="https://example.test/4",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="hash",
            indicator_value="a" * 64,
            pulse_id="pulse-5",
            pulse_name="Pulse 5",
            pulse_created=now,
            is_aggregator=False,
            source="source-a",
            source_url="https://example.test/5",
            fetched_at=now,
        ),
        ThreatIntelRef(
            indicator_type="hash",
            indicator_value="b" * 64,
            pulse_id="pulse-6",
            pulse_name="Pulse 6",
            pulse_created=now,
            is_aggregator=False,
            source="source-b",
            source_url="https://example.test/6",
            fetched_at=now,
        ),
    ]
    db.add_all(rows)
    db.commit()

    test_app = FastAPI()

    def override_get_session():
        yield db

    test_app.dependency_overrides[get_session] = override_get_session
    result = get_pulse_mix(db)
    assert result["total"] == 6
    assert len(result["by_indicator_type"]) == 3
    assert len(result["by_source"]) == 2
    assert abs(result["aggregator_ratio"] - (1 / 6)) < 1e-6
    assert result["stale_count"] == 0
    print("PASS")


if __name__ == "__main__":
    _self_test()