from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef, VulnAdvisory


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_feed_coverage(db: Session = Depends(get_session)) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)

    indicator_rows = db.execute(
        select(
            ThreatIntelRef.source.label("source"),
            func.count(func.distinct(ThreatIntelRef.indicator_value)).label("count"),
            func.count(func.distinct(ThreatIntelRef.pulse_id)).label("pulses"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetched_at"),
        )
        .group_by(ThreatIntelRef.source)
        .order_by(ThreatIntelRef.source.asc())
    ).all()

    indicators: list[dict[str, Any]] = []
    for source, count, pulses, last_fetched_at in indicator_rows:
        last_fetched_utc = _utc_datetime(last_fetched_at)
        stale_hours = None
        if last_fetched_utc is not None:
            stale_hours = round(
                max(0.0, (generated_at - last_fetched_utc).total_seconds() / 3600),
                2,
            )

        indicators.append(
            {
                "source": source,
                "count": int(count or 0),
                "pulses": int(pulses or 0),
                "last_fetched_at": last_fetched_at,
                "stale_hours": stale_hours,
            }
        )

    advisory_rows = db.execute(
        select(
            VulnAdvisory.feed.label("feed"),
            func.count().label("count"),
            func.max(VulnAdvisory.published_at).label("latest_published_at"),
        )
        .group_by(VulnAdvisory.feed)
        .order_by(VulnAdvisory.feed.asc())
    ).all()

    advisories = [
        {
            "feed": feed,
            "count": int(count or 0),
            "latest_published_at": latest_published_at,
        }
        for feed, count, latest_published_at in advisory_rows
    ]

    return {
        "indicators": indicators,
        "advisories": advisories,
        "generated_at": generated_at,
    }


get_threat_intel_feed_coverage = get_feed_coverage
get_feed_coverage_report = get_feed_coverage
get_coverage = get_feed_coverage


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE threat_intel_refs (
                id INTEGER PRIMARY KEY,
                indicator_type VARCHAR,
                indicator_value VARCHAR,
                pulse_id VARCHAR,
                pulse_name VARCHAR,
                source VARCHAR,
                source_url VARCHAR,
                is_aggregator BOOLEAN,
                fetched_at DATETIME
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE vuln_advisories (
                id INTEGER PRIMARY KEY,
                feed VARCHAR,
                severity VARCHAR,
                ecosystem VARCHAR,
                fetched_at DATETIME,
                published_at DATETIME
            )
            """
        )

    test_session_factory = sessionmaker(bind=test_engine)

    fetched_at = datetime.now(timezone.utc) - timedelta(hours=2)
    published_at = datetime.now(timezone.utc) - timedelta(hours=1)

    with test_session_factory() as session:
        session.execute(
            insert(ThreatIntelRef.__table__).values(
                indicator_type="ip",
                indicator_value="192.0.2.10",
                pulse_id="pulse-1",
                pulse_name="Example Pulse 1",
                source="alpha",
                source_url="https://alpha.example/pulse-1",
                is_aggregator=False,
                fetched_at=fetched_at,
            )
        )
        session.execute(
            insert(ThreatIntelRef.__table__).values(
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id="pulse-2",
                pulse_name="Example Pulse 2",
                source="beta",
                source_url="https://beta.example/pulse-2",
                is_aggregator=False,
                fetched_at=fetched_at,
            )
        )
        session.execute(
            insert(VulnAdvisory.__table__).values(
                feed="alpha-feed",
                severity="high",
                ecosystem="pypi",
                fetched_at=fetched_at,
                published_at=published_at,
            )
        )
        session.execute(
            insert(VulnAdvisory.__table__).values(
                feed="beta-feed",
                severity="medium",
                ecosystem="npm",
                fetched_at=fetched_at,
                published_at=published_at,
            )
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/threat-intel/feed-coverage")
    def feed_coverage_endpoint(db: Session = Depends(get_session)):
        return get_feed_coverage(db)

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/feed-coverage")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["indicators"]) == 2
        assert len(payload["advisories"]) == 2
        assert payload["indicators"][0]["count"] >= 1

    print("PASS")