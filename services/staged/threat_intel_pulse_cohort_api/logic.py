from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import get_session
from app.models import ThreatIntelRef
from sqlalchemy import case, desc, func, select
from sqlalchemy.orm import Session


DEFAULT_DAYS = 30
DEFAULT_LIMIT = 100


def get_pulse_cohort_cutoff(days: int = DEFAULT_DAYS) -> datetime:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)


def get_pulse_cohort(
    session: Session,
    days: int = DEFAULT_DAYS,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    cutoff = get_pulse_cohort_cutoff(days)

    indicator_count = func.count(ThreatIntelRef.id)
    aggregator_count = func.sum(
        case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)
    )

    statement = (
        select(
            ThreatIntelRef.pulse_id.label("pulse_id"),
            ThreatIntelRef.pulse_name.label("pulse_name"),
            indicator_count.label("indicators"),
            aggregator_count.label("aggregator_indicators"),
            func.min(ThreatIntelRef.pulse_created).label("first_pulse"),
            func.max(ThreatIntelRef.pulse_created).label("last_pulse"),
        )
        .where(ThreatIntelRef.pulse_created >= cutoff)
        .group_by(ThreatIntelRef.pulse_id, ThreatIntelRef.pulse_name)
        .order_by(desc(indicator_count))
        .limit(limit)
    )

    rows = session.execute(statement).mappings().all()
    return [dict(row) for row in rows]


def get_threat_intel_pulse_cohort(
    session: Session,
    days: int = DEFAULT_DAYS,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    return get_pulse_cohort(session, days=days, limit=limit)


def get_pulse_cohort_data(
    session: Session,
    days: int = DEFAULT_DAYS,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    return {
        "cutoff": get_pulse_cohort_cutoff(days),
        "pulses": get_pulse_cohort(session, days=days, limit=limit),
    }


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import (
        Boolean,
        Column,
        DateTime,
        Integer,
        MetaData,
        String,
        Table,
        create_engine,
    )
    from sqlalchemy.orm import Session as OrmSession
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()
    local_threat_intel_refs = Table(
        "threat_intel_refs",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("indicator_type", String, nullable=False),
        Column("indicator_value", String, nullable=False),
        Column("pulse_id", String),
        Column("pulse_name", String),
        Column("pulse_created", DateTime),
        Column("is_aggregator", Boolean),
        Column("source", String),
        Column("source_url", String),
        Column("fetched_at", DateTime),
    )
    metadata.create_all(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            local_threat_intel_refs.insert(),
            [
                {
                    "id": 1,
                    "indicator_type": "ip",
                    "indicator_value": "192.0.2.1",
                    "pulse_id": "pulse-active",
                    "pulse_name": "Active Pulse",
                    "pulse_created": now - timedelta(days=1),
                    "is_aggregator": True,
                    "source": "test",
                    "source_url": "https://example.invalid/1",
                    "fetched_at": now,
                },
                {
                    "id": 2,
                    "indicator_type": "domain",
                    "indicator_value": "example.test",
                    "pulse_id": "pulse-active",
                    "pulse_name": "Active Pulse",
                    "pulse_created": now - timedelta(days=2),
                    "is_aggregator": False,
                    "source": "test",
                    "source_url": "https://example.invalid/2",
                    "fetched_at": now,
                },
                {
                    "id": 3,
                    "indicator_type": "url",
                    "indicator_value": "https://example.test/a",
                    "pulse_id": "pulse-active",
                    "pulse_name": "Active Pulse",
                    "pulse_created": now - timedelta(days=3),
                    "is_aggregator": False,
                    "source": "test",
                    "source_url": "https://example.invalid/3",
                    "fetched_at": now,
                },
                {
                    "id": 4,
                    "indicator_type": "hash",
                    "indicator_value": "abc123",
                    "pulse_id": "pulse-active",
                    "pulse_name": "Active Pulse",
                    "pulse_created": now - timedelta(days=4),
                    "is_aggregator": False,
                    "source": "test",
                    "source_url": "https://example.invalid/4",
                    "fetched_at": now,
                },
                {
                    "id": 5,
                    "indicator_type": "ip",
                    "indicator_value": "198.51.100.1",
                    "pulse_id": "pulse-old",
                    "pulse_name": "Old Pulse",
                    "pulse_created": now - timedelta(days=45),
                    "is_aggregator": True,
                    "source": "test",
                    "source_url": "https://example.invalid/5",
                    "fetched_at": now,
                },
            ],
        )

    session_factory = OrmSession
    local_app = FastAPI()

    def override_get_session():
        with session_factory(engine) as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/threat-intel/pulse-cohort")
    def pulse_cohort(
        days: int = 30,
        session: OrmSession = Depends(get_session),
    ):
        cutoff = get_pulse_cohort_cutoff(days)
        pulses = get_pulse_cohort(session, days=days, limit=DEFAULT_LIMIT)
        return {
            "days": days,
            "cutoff": cutoff.isoformat(),
            "pulses": pulses,
        }

    response = TestClient(local_app).get(
        "/api/threat-intel/pulse-cohort",
        params={"days": 30},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["days"] == 30
    assert payload["cutoff"]
    assert len(payload["pulses"]) == 1
    pulse = payload["pulses"][0]
    assert pulse["pulse_id"] == "pulse-active"
    assert pulse["indicators"] == 4
    assert pulse["aggregator_indicators"] == 1
    assert all(item["pulse_id"] != "pulse-old" for item in payload["pulses"])
    print("PASS")