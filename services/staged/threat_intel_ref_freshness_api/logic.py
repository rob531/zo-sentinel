from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_ref_freshness(
    db: Session = Depends(get_session),
    max_age_hours: int = 48,
) -> dict[str, Any]:
    if max_age_hours < 0:
        raise ValueError("max_age_hours must be non-negative")

    rows = db.execute(
        select(
            ThreatIntelRef.id,
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.source,
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.fetched_at,
        )
    ).all()

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    indicator_types: set[str] = set()
    sources: dict[str | None, dict[str, Any]] = {}
    stale_refs = 0
    newest_fetched_at: datetime | None = None

    for row in rows:
        indicator_type = row.indicator_type
        source = row.source
        fetched_at = _utc_datetime(row.fetched_at)

        if indicator_type is not None:
            indicator_types.add(indicator_type)

        if fetched_at is not None:
            if fetched_at < cutoff:
                stale_refs += 1
            if newest_fetched_at is None or fetched_at > newest_fetched_at:
                newest_fetched_at = fetched_at

        source_stats = sources.setdefault(
            source,
            {
                "source": source,
                "refs": 0,
                "stale_refs": 0,
                "newest_fetched_at": None,
                "pulse_ids": set(),
            },
        )
        source_stats["refs"] += 1
        if fetched_at is not None and fetched_at < cutoff:
            source_stats["stale_refs"] += 1
        if fetched_at is not None:
            current_newest = source_stats["newest_fetched_at"]
            if current_newest is None or fetched_at > current_newest:
                source_stats["newest_fetched_at"] = fetched_at
        if row.pulse_id is not None:
            source_stats["pulse_ids"].add(row.pulse_id)

    source_breakdown = []
    for source_stats in sorted(
        sources.values(),
        key=lambda item: (item["source"] is None, str(item["source"]).lower()),
    ):
        source_breakdown.append(
            {
                "source": source_stats["source"],
                "refs": source_stats["refs"],
                "stale_refs": source_stats["stale_refs"],
                "newest_fetched_at": source_stats["newest_fetched_at"],
                "pulse_count": len(source_stats["pulse_ids"]),
            }
        )

    total_refs = len(rows)
    distinct_sources = sum(1 for source in sources if source is not None)

    return {
        "max_age_hours": max_age_hours,
        "total_refs": total_refs,
        "distinct_indicator_types": len(indicator_types),
        "distinct_sources": distinct_sources,
        "stale_refs": stale_refs,
        "stale_pct": round((stale_refs / total_refs * 100.0) if total_refs else 0.0, 2),
        "newest_fetched_at": newest_fetched_at,
        "sources": source_breakdown,
    }


def compute_ref_freshness(
    db: Session = Depends(get_session),
    max_age_hours: int = 48,
) -> dict[str, Any]:
    return get_ref_freshness(db=db, max_age_hours=max_age_hours)


def get_freshness_stats(
    db: Session = Depends(get_session),
    max_age_hours: int = 48,
) -> dict[str, Any]:
    return get_ref_freshness(db=db, max_age_hours=max_age_hours)


def get_freshness_data(
    db: Session = Depends(get_session),
    max_age_hours: int = 48,
) -> dict[str, Any]:
    return get_ref_freshness(db=db, max_age_hours=max_age_hours)


def get_ref_freshness_stats(
    db: Session = Depends(get_session),
    max_age_hours: int = 48,
) -> dict[str, Any]:
    return get_ref_freshness(db=db, max_age_hours=max_age_hours)


def get_threat_intel_ref_freshness(
    db: Session = Depends(get_session),
    max_age_hours: int = 48,
) -> dict[str, Any]:
    return get_ref_freshness(db=db, max_age_hours=max_age_hours)


if __name__ == "__main__":
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
    TestingSession = sessionmaker(bind=engine, autoflush=False)

    now = datetime.now(timezone.utc)
    with TestingSession() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="abuse-1",
                    pulse_name="Abuse pulse",
                    pulse_created=now,
                    is_aggregator=True,
                    source="abuse.ch",
                    source_url="https://abuse.ch/example/1",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="abuse-2",
                    pulse_name="Abuse domain pulse",
                    pulse_created=now,
                    is_aggregator=True,
                    source="abuse.ch",
                    source_url="https://abuse.ch/example/2",
                    fetched_at=now - timedelta(hours=1),
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="198.51.100.2",
                    pulse_id="otx-1",
                    pulse_name="OTX pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="otx",
                    source_url="https://otx.example/1",
                    fetched_at=now - timedelta(hours=72),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/threat-intel/ref-freshness")
    def threat_intel_ref_freshness(
        max_age_hours: int = Query(48),
        db: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_ref_freshness(db=db, max_age_hours=max_age_hours)

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/ref-freshness", params={"max_age_hours": 48})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_refs"] == 3
        assert payload["distinct_sources"] == 2
        assert payload["stale_refs"] >= 1
        assert payload["stale_pct"] > 0
        assert len(payload["sources"]) == 2
        assert all("refs" in source for source in payload["sources"])

    print("PASS")