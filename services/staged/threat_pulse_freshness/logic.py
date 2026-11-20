from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def threat_pulse_freshness(db: Session) -> Dict[str, Any]:
    """Summarize threat-intel pulse freshness by source."""
    rows = db.execute(
        select(
            ThreatIntelRef.source,
            ThreatIntelRef.pulse_created,
            ThreatIntelRef.is_aggregator,
        )
    ).all()

    grouped: Dict[str, Dict[str, Any]] = {}
    now = datetime.now(timezone.utc)

    for source, pulse_created, is_aggregator in rows:
        source_name = source or "unknown"
        item = grouped.setdefault(
            source_name,
            {
                "latest": None,
                "ref_count": 0,
                "aggregator_count": 0,
            },
        )
        item["ref_count"] += 1
        if is_aggregator:
            item["aggregator_count"] += 1

        if pulse_created is not None:
            latest = pulse_created
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            else:
                latest = latest.astimezone(timezone.utc)
            if item["latest"] is None or latest > item["latest"]:
                item["latest"] = latest

    sources: list[Dict[str, Any]] = []
    stalest_source: Optional[str] = None
    stalest_created: Optional[datetime] = None
    stale_over_24h = 0

    for source_name in sorted(grouped):
        item = grouped[source_name]
        latest: Optional[datetime] = item["latest"]
        age_hours: Optional[float] = None

        if latest is not None:
            age_hours = round((now - latest).total_seconds() / 3600.0, 2)
            if age_hours > 24:
                stale_over_24h += 1
            if stalest_created is None or latest < stalest_created:
                stalest_created = latest
                stalest_source = source_name

        sources.append(
            {
                "source": source_name,
                "latest_pulse_created": latest,
                "age_hours": age_hours,
                "ref_count": item["ref_count"],
                "aggregator_count": item["aggregator_count"],
            }
        )

    return {
        "sources": sources,
        "total_sources": len(sources),
        "stalest_source": stalest_source,
        "stale_over_24h": stale_over_24h,
    }


compute_threat_pulse_freshness = threat_pulse_freshness


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threat/pulse-freshness")
    def pulse_freshness_endpoint(db: Session = Depends(get_session)):
        return threat_pulse_freshness(db)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id=101,
                    pulse_name="Current pulse",
                    pulse_created=now - timedelta(hours=2),
                    is_aggregator=True,
                    source="alpha",
                    source_url="https://alpha.example/pulse/101",
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.org",
                    pulse_id=102,
                    pulse_name="Current pulse",
                    pulse_created=now - timedelta(hours=2),
                    is_aggregator=False,
                    source="alpha",
                    source_url="https://alpha.example/pulse/102",
                ),
                ThreatIntelRef(
                    indicator_type="url",
                    indicator_value="https://old.example/item",
                    pulse_id=103,
                    pulse_name="Old pulse",
                    pulse_created=now - timedelta(hours=50),
                    is_aggregator=False,
                    source="bravo",
                    source_url="https://bravo.example/pulse/103",
                ),
            ]
        )
        session.commit()

    response = TestClient(that_app).get("/api/threat/pulse-freshness")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_sources"] == 2, payload
    assert sum(item["ref_count"] for item in payload["sources"]) == 3, payload
    assert payload["stale_over_24h"] == 1, payload
    print("PASS")