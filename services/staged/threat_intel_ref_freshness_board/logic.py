"""Logic for threat-intel reference freshness and provenance reporting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ThreatIntelRef


def _as_utc(value: Any) -> datetime:
    """Normalize a fetched_at value for Python-side age comparisons."""
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise TypeError(f"Unsupported fetched_at value: {value!r}")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _isoformat(value: Any) -> str:
    return _as_utc(value).isoformat()


def threat_intel_ref_freshness(session: Session, max_age_days: int = 30) -> dict:
    """Return freshness and provenance statistics for ingested references."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(max_age_days))
    refs = list(session.execute(select(ThreatIntelRef)).scalars().all())

    by_indicator_type: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    stale_by_source: Dict[str, List[datetime]] = {}
    stale_refs = 0
    aggregator_refs = 0

    for ref in refs:
        indicator_type = ref.indicator_type or "unknown"
        source = ref.source or "unknown"

        by_indicator_type[indicator_type] = by_indicator_type.get(indicator_type, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1

        if ref.is_aggregator:
            aggregator_refs += 1

        fetched_at = _as_utc(ref.fetched_at)
        if fetched_at < cutoff:
            stale_refs += 1
            stale_by_source.setdefault(source, []).append(fetched_at)

    stale_sources = [
        {
            "source": source,
            "stale_count": len(fetched_ats),
            "oldest_fetched_at": _isoformat(min(fetched_ats)),
        }
        for source, fetched_ats in sorted(stale_by_source.items())
    ]

    total_refs = len(refs)
    return {
        "total_refs": total_refs,
        "stale_refs": stale_refs,
        "stale_pct": round((stale_refs / total_refs) * 100, 2) if total_refs else 0.0,
        "max_age_days": int(max_age_days),
        "by_indicator_type": dict(sorted(by_indicator_type.items())),
        "by_source": dict(sorted(by_source.items())),
        "aggregator_refs": aggregator_refs,
        "aggregator_pct": round((aggregator_refs / total_refs) * 100, 2)
        if total_refs
        else 0.0,
        "stale_sources": stale_sources,
    }


if __name__ == "__main__":
    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine)

    db = TestingSession()
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-old",
                pulse_name="Old pulse",
                is_aggregator=False,
                source="old-source",
                source_url="https://example.test/old",
                fetched_at=now - timedelta(days=90),
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.com",
                pulse_id="pulse-one",
                pulse_name="Recent pulse",
                is_aggregator=True,
                source="recent-source",
                source_url="https://example.test/recent",
                fetched_at=now - timedelta(days=2),
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.org",
                pulse_id="pulse-two",
                pulse_name="Recent pulse",
                is_aggregator=False,
                source="recent-source",
                source_url="https://example.test/recent-two",
                fetched_at=now - timedelta(days=5),
            ),
            ThreatIntelRef(
                indicator_type="hash",
                indicator_value="abc123",
                pulse_id="pulse-three",
                pulse_name="Recent pulse",
                is_aggregator=False,
                source="another-source",
                source_url="https://example.test/another",
                fetched_at=now - timedelta(days=1),
            ),
        ]
    )
    db.commit()

    test_app = FastAPI()

    @test_app.get("/api/threat-intel/refs/freshness")
    def freshness_endpoint(
        max_age_days: int = Query(30),
        session: Session = Depends(get_session),
    ):
        return threat_intel_ref_freshness(session, max_age_days)

    def override_get_session():
        yield db

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/threat-intel/refs/freshness")
    payload = response.json()
    assert response.status_code == 200
    assert payload["total_refs"] == 4
    assert payload["stale_refs"] == 1
    assert payload["aggregator_refs"] == 1
    print("PASS")
    db.close()
    engine.dispose()