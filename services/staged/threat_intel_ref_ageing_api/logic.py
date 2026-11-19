"""logic.py -- threat-intel reference ageing data and computation layer."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


_BUCKETS = ("0-7d", "8-30d", "31-90d", "90d+")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_for(age_days: float) -> str:
    if age_days <= 7:
        return "0-7d"
    if age_days <= 30:
        return "8-30d"
    if age_days <= 90:
        return "31-90d"
    return "90d+"


def threat_intel_ref_ageing(
    days: int = 90,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """Return the age distribution of fetched threat-intel references."""
    if isinstance(days, Session) and db is None:
        db = days
        days = 90

    session_context: Any = None
    close_context = False

    if db is None:
        session_context = get_session()
        if hasattr(session_context, "__next__"):
            db = next(session_context)
            close_context = True
        else:
            db = session_context

    try:
        now = datetime.now(timezone.utc)
        rows = db.execute(
            select(
                ThreatIntelRef.fetched_at,
                ThreatIntelRef.source,
                ThreatIntelRef.is_aggregator,
            )
        ).all()

        bucket_counts = {bucket: 0 for bucket in _BUCKETS}
        source_counts: Dict[str, int] = {}

        for fetched_at, source, is_aggregator in rows:
            age_days = max(0.0, (now - _as_utc(fetched_at)).total_seconds() / 86400.0)
            bucket_counts[_bucket_for(age_days)] += 1

            if not is_aggregator:
                source_name = source or "unknown"
                source_counts[source_name] = source_counts.get(source_name, 0) + 1

        return {
            "days": int(days),
            "buckets": [
                {"bucket": bucket, "count": bucket_counts[bucket]}
                for bucket in _BUCKETS
            ],
            "by_source": [
                {"source": source, "count": source_counts[source]}
                for source in sorted(source_counts)
            ],
            "total": len(rows),
        }
    finally:
        if close_context and session_context is not None:
            close = getattr(session_context, "close", None)
            if close is not None:
                close()
        elif db is not None and session_context is None:
            close = getattr(db, "close", None)
            if close is not None and db is not get_session:
                close()


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = session_factory()

    now = datetime.now(timezone.utc)
    rows = [
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.1",
            source="alpha",
            source_url="https://alpha.invalid/1",
            pulse_id="pulse-1",
            fetched_at=now - timedelta(days=3),
            is_aggregator=False,
        ),
        ThreatIntelRef(
            indicator_type="ip",
            indicator_value="192.0.2.2",
            source="beta",
            source_url="https://beta.invalid/2",
            pulse_id="pulse-2",
            fetched_at=now - timedelta(days=15),
            is_aggregator=True,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.test",
            source="alpha",
            source_url="https://alpha.invalid/3",
            pulse_id="pulse-3",
            fetched_at=now - timedelta(days=60),
            is_aggregator=False,
        ),
        ThreatIntelRef(
            indicator_type="domain",
            indicator_value="example.org",
            source="beta",
            source_url="https://beta.invalid/4",
            pulse_id="pulse-4",
            fetched_at=now - timedelta(days=120),
            is_aggregator=False,
        ),
    ]
    db.add_all(rows)
    db.commit()

    test_app = FastAPI()

    @test_app.get("/api/threat-intel/ref-ageing")
    def ref_ageing(
        days: int = 90,
        session: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return threat_intel_ref_ageing(days=days, db=session)

    def override_session():
        yield db

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/ref-ageing?days=90")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert all(item["count"] == 1 for item in payload["buckets"])
        assert len(payload["by_source"]) == 2

    db.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()