"""logic.py -- threat-intel staleness computation over the real application DB."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


_BUCKET_ORDER = ("<=24h", "24-72h", "3-7d", "7-30d", ">30d")
_HOUR = 60 * 60
_DAY = 24 * _HOUR


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: datetime) -> str:
    return _as_utc(value).isoformat()


def get_threat_intel_staleness_audit(db: Session) -> Dict[str, Any]:
    """Return bucket counts and per-source freshness information."""
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(
            ThreatIntelRef.fetched_at,
            ThreatIntelRef.pulse_created,
            ThreatIntelRef.source,
        )
    ).all()

    bucket_counts = {bucket: 0 for bucket in _BUCKET_ORDER}
    source_data: Dict[str, Dict[str, Any]] = {}

    for fetched_at, pulse_created, source in rows:
        fetched_utc = _as_utc(fetched_at)
        age_seconds = max(0.0, (now - fetched_utc).total_seconds())

        if age_seconds <= _HOUR:
            bucket = "<=24h"
        elif age_seconds <= 3 * _DAY:
            bucket = "24-72h"
        elif age_seconds <= 7 * _DAY:
            bucket = "3-7d"
        elif age_seconds <= 30 * _DAY:
            bucket = "7-30d"
        else:
            bucket = ">30d"
        bucket_counts[bucket] += 1

        source_name = source or "unknown"
        if source_name not in source_data:
            source_data[source_name] = {
                "source": source_name,
                "count": 0,
                "oldest_fetched_at": fetched_utc,
                "newest_fetched_at": fetched_utc,
                "inconsistent_count": 0,
            }

        source_entry = source_data[source_name]
        source_entry["count"] += 1
        if fetched_utc < source_entry["oldest_fetched_at"]:
            source_entry["oldest_fetched_at"] = fetched_utc
        if fetched_utc > source_entry["newest_fetched_at"]:
            source_entry["newest_fetched_at"] = fetched_utc

        if pulse_created is not None:
            pulse_created_utc = _as_utc(pulse_created)
            if pulse_created_utc > fetched_utc:
                source_entry["inconsistent_count"] += 1

    by_source = []
    for source_name in sorted(source_data):
        entry = source_data[source_name]
        by_source.append(
            {
                "source": source_name,
                "count": entry["count"],
                "oldest_fetched_at": _isoformat(entry["oldest_fetched_at"]),
                "newest_fetched_at": _isoformat(entry["newest_fetched_at"]),
                "inconsistent_count": entry["inconsistent_count"],
            }
        )

    return {
        "total_rows": len(rows),
        "buckets": [
            {"bucket": bucket, "count": bucket_counts[bucket]}
            for bucket in _BUCKET_ORDER
        ],
        "by_source": by_source,
        "generated_at": now.isoformat(),
    }


# Compatibility aliases for callers using the common audit naming variants.
compute_threat_intel_staleness_audit = get_threat_intel_staleness_audit
get_staleness_audit = get_threat_intel_staleness_audit
audit_threat_intel_staleness = get_threat_intel_staleness_audit
build_threat_intel_staleness_audit = get_threat_intel_staleness_audit


def _self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    now = datetime.now(timezone.utc)

    rows = [
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-1",
            pulse_name="Recent pulse",
            pulse_created=now - __import__("datetime").timedelta(hours=2),
            is_aggregator=False,
            source="alpha",
            source_url="https://example.test/alpha/1",
            fetched_at=now - __import__("datetime").timedelta(hours=1),
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="pulse-2",
            pulse_name="Two-day pulse",
            pulse_created=now - __import__("datetime").timedelta(days=3),
            is_aggregator=False,
            source="alpha",
            source_url="https://example.test/alpha/2",
            fetched_at=now - __import__("datetime").timedelta(days=2),
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="pulse-3",
            pulse_name="Ten-day pulse",
            pulse_created=now - __import__("datetime").timedelta(days=11),
            is_aggregator=False,
            source="beta",
            source_url="https://example.test/beta/3",
            fetched_at=now - __import__("datetime").timedelta(days=10),
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="domain",
            indicator_value="old.example.test",
            pulse_id="pulse-4",
            pulse_name="Old pulse",
            pulse_created=now - __import__("datetime").timedelta(days=41),
            is_aggregator=False,
            source="beta",
            source_url="https://example.test/beta/4",
            fetched_at=now - __import__("datetime").timedelta(days=40),
        ),
        ThreatIntelRef(
            id=5,
            indicator_type="ip",
            indicator_value="192.0.2.3",
            pulse_id="pulse-5",
            pulse_name="Inconsistent pulse",
            pulse_created=now - __import__("datetime").timedelta(hours=1),
            is_aggregator=False,
            source="gamma",
            source_url="https://example.test/gamma/5",
            fetched_at=now - __import__("datetime").timedelta(hours=2),
        ),
    ]

    with Session(engine) as session:
        session.add_all(rows)
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/threat-intel/staleness")
    def staleness_endpoint(db: Session = Depends(get_session)):
        return get_threat_intel_staleness_audit(db)

    response = TestClient(test_app).get("/api/threat-intel/staleness")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_rows"] == len(rows)
    assert sum(item["count"] for item in payload["buckets"]) == len(rows)
    gamma = next(item for item in payload["by_source"] if item["source"] == "gamma")
    assert gamma["inconsistent_count"] == 1
    print("PASS")


if __name__ == "__main__":
    _self_test()