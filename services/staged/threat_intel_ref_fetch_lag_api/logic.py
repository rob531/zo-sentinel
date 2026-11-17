from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _age_hours(value: datetime | None, now: datetime) -> float | None:
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)

    age = max(0.0, (now - value).total_seconds() / 3600.0)
    return round(age, 2)


def get_fetch_lag(
    session: Session = Depends(get_session),
    hours: int = 168,
    source: str | None = None,
) -> dict[str, Any]:
    """Return aggregate freshness information for the threat-intel cache."""
    now = _utc_now()
    cutoff = (now - timedelta(hours=hours)).replace(tzinfo=None)

    source_statement = (
        select(
            ThreatIntelRef.source.label("source"),
            func.count(ThreatIntelRef.id).label("total"),
            func.sum(
                case(
                    (ThreatIntelRef.fetched_at < cutoff, 1),
                    else_=0,
                )
            ).label("stale_count"),
            func.min(ThreatIntelRef.fetched_at).label("oldest_fetched_at"),
        )
        .group_by(ThreatIntelRef.source)
    )

    indicator_statement = (
        select(
            ThreatIntelRef.indicator_type.label("indicator_type"),
            func.count(ThreatIntelRef.id).label("count"),
            func.min(ThreatIntelRef.fetched_at).label("oldest_fetched_at"),
        )
        .group_by(ThreatIntelRef.indicator_type)
    )

    if source is not None:
        source_statement = source_statement.where(ThreatIntelRef.source == source)
        indicator_statement = indicator_statement.where(
            ThreatIntelRef.source == source
        )

    source_rows = session.execute(source_statement).all()
    indicator_rows = session.execute(indicator_statement).all()

    by_source: list[dict[str, Any]] = []
    overall_max_age: float | None = None
    total = 0

    for row in source_rows:
        row_total = int(row.total or 0)
        row_max_age = _age_hours(row.oldest_fetched_at, now)
        total += row_total

        if row_max_age is not None:
            if overall_max_age is None or row_max_age > overall_max_age:
                overall_max_age = row_max_age

        by_source.append(
            {
                "source": row.source,
                "total": row_total,
                "stale_count": int(row.stale_count or 0),
                "max_age_hours": row_max_age,
                "oldest_fetched_at": (
                    row.oldest_fetched_at.isoformat()
                    if row.oldest_fetched_at is not None
                    else None
                ),
            }
        )

    by_source.sort(key=lambda item: (-item["total"], item["source"] or ""))

    by_indicator_type: list[dict[str, Any]] = []
    for row in indicator_rows:
        by_indicator_type.append(
            {
                "indicator_type": row.indicator_type,
                "count": int(row.count or 0),
                "max_age_hours": _age_hours(row.oldest_fetched_at, now),
            }
        )

    by_indicator_type.sort(
        key=lambda item: (-item["count"], item["indicator_type"] or "")
    )

    return {
        "hours": hours,
        "generated_at": now.isoformat(),
        "by_source": by_source,
        "by_indicator_type": by_indicator_type,
        "overall_max_age_hours": overall_max_age,
        "total": total,
    }


fetch_lag = get_fetch_lag
get_threat_intel_fetch_lag = get_fetch_lag
get_fetch_lag_data = get_fetch_lag


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_app = FastAPI()
    test_engine = create_engine("sqlite:///:memory:")
    ThreatIntelRef.__table__.create(test_engine)
    test_session_factory = sessionmaker(bind=test_engine)

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    now = _utc_now()
    test_rows = [
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="p1",
            pulse_name="pulse",
            pulse_created=now,
            is_aggregator=False,
            source="abuse.ch",
            source_url="https://abuse.ch",
            fetched_at=now - timedelta(hours=2),
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="p2",
            pulse_name="pulse",
            pulse_created=now,
            is_aggregator=False,
            source="abuse.ch",
            source_url="https://abuse.ch",
            fetched_at=now - timedelta(hours=2),
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="ip",
            indicator_value="192.0.2.3",
            pulse_id="p3",
            pulse_name="pulse",
            pulse_created=now,
            is_aggregator=False,
            source="abuse.ch",
            source_url="https://abuse.ch",
            fetched_at=now - timedelta(hours=2),
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="ip",
            indicator_value="198.51.100.1",
            pulse_id="p4",
            pulse_name="pulse",
            pulse_created=now,
            is_aggregator=False,
            source="circl.lu",
            source_url="https://circl.lu",
            fetched_at=now - timedelta(hours=400),
        ),
        ThreatIntelRef(
            id=5,
            indicator_type="ip",
            indicator_value="198.51.100.2",
            pulse_id="p5",
            pulse_name="pulse",
            pulse_created=now,
            is_aggregator=False,
            source="circl.lu",
            source_url="https://circl.lu",
            fetched_at=now - timedelta(hours=400),
        ),
        ThreatIntelRef(
            id=6,
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="p6",
            pulse_name="pulse",
            pulse_created=now,
            is_aggregator=False,
            source="abuse.ch",
            source_url="https://abuse.ch",
            fetched_at=now - timedelta(hours=10),
        ),
    ]

    with test_session_factory() as session:
        session.add_all(test_rows)
        session.commit()
        result = get_fetch_lag(session=session, hours=168)

        assert result["total"] == 6
        circl = next(item for item in result["by_source"] if item["source"] == "circl.lu")
        assert circl["stale_count"] == 2
        assert circl["max_age_hours"] >= 400
        domain = next(
            item
            for item in result["by_indicator_type"]
            if item["indicator_type"] == "domain"
        )
        assert domain["count"] == 1

    print("PASS")