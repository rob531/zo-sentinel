"""logic.py -- threat-intel reference ageing data and computation layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Integer, cast, func, literal, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


_BUCKETS = ("0-7d", "8-30d", "31-90d", "90d+")


def _bucket_for_age(age_days: float) -> str:
    if age_days <= 7:
        return "0-7d"
    if age_days <= 30:
        return "8-30d"
    if age_days <= 90:
        return "31-90d"
    return "90d+"


def _python_age_days(fetched_at: datetime) -> float:
    now = datetime.now(timezone.utc)
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return max(0.0, (now - fetched_at).total_seconds() / 86400.0)


def threat_intel_ref_ageing(db: Session, days: int = 90) -> dict[str, Any]:
    """Return reference-age buckets, non-aggregator source counts, and total."""
    dialect = db.get_bind().dialect.name

    if dialect == "postgresql":
        age_expression = cast(
            func.floor(
                func.extract(
                    "epoch",
                    func.current_timestamp - ThreatIntelRef.fetched_at,
                )
                / literal(1000)
            ),
            Integer,
        )
        rows = db.execute(
            select(
                ThreatIntelRef.source,
                ThreatIntelRef.is_aggregator,
                age_expression.label("age_days"),
            )
        ).all()
    else:
        rows = db.execute(
            select(
                ThreatIntelRef.source,
                ThreatIntelRef.is_aggregator,
                ThreatIntelRef.fetched_at,
            )
        ).all()

    bucket_counts = {bucket: 0 for bucket in _BUCKETS}
    source_counts: dict[str, int] = {}
    total = 0

    for source, is_aggregator, age_value in rows:
        total += 1
        age_days = (
            float(age_value)
            if dialect == "postgresql"
            else _python_age_days(age_value)
        )
        bucket_counts[_bucket_for_age(age_days)] += 1

        if not bool(is_aggregator):
            source_name = str(source) if source is not None else "unknown"
            source_counts[source_name] = source_counts.get(source_name, 0) + 1

    return {
        "days": int(days),
        "buckets": [
            {"bucket": bucket, "count": bucket_counts[bucket]}
            for bucket in _BUCKETS
        ],
        "by_source": [
            {"source": source, "count": count}
            for source, count in sorted(source_counts.items())
        ],
        "total": total,
    }


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    ThreatIntelRef.__table__.create(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    test_rows = [
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="192.0.2.1",
            is_aggregator=False,
            fetched_at=now - timedelta(days=3),
            pulse_created=now,
            pulse_id=1,
            pulse_name="pulse-1",
            source="alpha",
            source_url="https://alpha.example/1",
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="domain",
            indicator_value="alpha.example",
            is_aggregator=True,
            fetched_at=now - timedelta(days=15),
            pulse_created=now,
            pulse_id=1,
            pulse_name="pulse-1",
            source="alpha",
            source_url="https://alpha.example/2",
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="hash",
            indicator_value="a" * 64,
            is_aggregator=False,
            fetched_at=now - timedelta(days=60),
            pulse_created=now,
            pulse_id=2,
            pulse_name="pulse-2",
            source="beta",
            source_url="https://beta.example/1",
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="url",
            indicator_value="https://beta.example/item",
            is_aggregator=False,
            fetched_at=now - timedelta(days=120),
            pulse_created=now,
            pulse_id=2,
            pulse_name="pulse-2",
            source="beta",
            source_url="https://beta.example/2",
        ),
    ]

    with test_session_factory() as session:
        session.add_all(test_rows)
        session.commit()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/threat-intel/ref-ageing")
    def ref_ageing(days: int = 90, db: Session = Depends(get_session)):
        return threat_intel_ref_ageing(db=db, days=days)

    response = TestClient(test_app).get(
        "/api/threat-intel/ref-ageing",
        params={"days": 90},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["days"] == 90
    assert payload["total"] == 4
    assert {item["bucket"]: item["count"] for item in payload["buckets"]} == {
        "0-7d": 1,
        "8-30d": 1,
        "31-90d": 1,
        "90d+": 1,
    }
    assert len(payload["by_source"]) == 2
    assert {item["source"]: item["count"] for item in payload["by_source"]} == {
        "alpha": 1,
        "beta": 2,
    }
    print("PASS")