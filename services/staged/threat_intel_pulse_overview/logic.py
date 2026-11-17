from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _iso8601(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def get_threat_intel_refs(
    session: Session,
    include_aggregators: bool = False,
) -> list[ThreatIntelRef]:
    stmt = select(ThreatIntelRef).order_by(
        ThreatIntelRef.pulse_created.desc().nullslast(),
        ThreatIntelRef.pulse_id.asc(),
        ThreatIntelRef.id.asc(),
    )
    if not include_aggregators:
        stmt = stmt.where(ThreatIntelRef.is_aggregator.is_(False))
    return list(session.scalars(stmt).all())


def get_pulse_overview(
    session: Session,
    limit: int = 50,
    include_aggregators: bool = False,
) -> dict[str, Any]:
    bounded_limit = max(1, min(int(limit), 200))

    indicator_count = func.count().label("indicator_count")
    distinct_type_count = func.count(
        distinct(ThreatIntelRef.indicator_type)
    ).label("distinct_type_count")

    stmt = (
        select(
            ThreatIntelRef.pulse_id.label("pulse_id"),
            func.max(ThreatIntelRef.pulse_name).label("pulse_name"),
            func.max(ThreatIntelRef.pulse_created).label("pulse_created"),
            func.max(ThreatIntelRef.fetched_at).label("fetched_at"),
            indicator_count,
            distinct_type_count,
            func.max(ThreatIntelRef.source).label("source"),
        )
        .select_from(ThreatIntelRef)
        .group_by(ThreatIntelRef.pulse_id)
    )

    if not include_aggregators:
        stmt = stmt.where(ThreatIntelRef.is_aggregator.is_(False))

    nulls_last = case(
        (ThreatIntelRef.pulse_created.is_(None), 1),
        else_=0,
    )
    stmt = (
        stmt.order_by(
            nulls_last.asc(),
            ThreatIntelRef.pulse_created.desc(),
            indicator_count.desc(),
            ThreatIntelRef.pulse_id.asc(),
        )
        .limit(bounded_limit)
    )

    rows = list(session.execute(stmt).mappings().all())
    pulse_ids = [row["pulse_id"] for row in rows]
    types_by_pulse: dict[str, set[str]] = {pulse_id: set() for pulse_id in pulse_ids}

    if pulse_ids:
        type_stmt = (
            select(
                ThreatIntelRef.pulse_id.label("pulse_id"),
                ThreatIntelRef.indicator_type.label("indicator_type"),
            )
            .where(ThreatIntelRef.pulse_id.in_(pulse_ids))
            .order_by(
                ThreatIntelRef.pulse_id.asc(),
                ThreatIntelRef.indicator_type.asc(),
            )
        )
        for type_row in session.execute(type_stmt).mappings().all():
            indicator_type = type_row["indicator_type"]
            if indicator_type is not None:
                types_by_pulse[type_row["pulse_id"]].add(str(indicator_type))

    pulses: list[dict[str, Any]] = []
    for row in rows:
        pulse_id = str(row["pulse_id"])
        indicator_types = sorted(types_by_pulse.get(pulse_id, set()))
        pulses.append(
            {
                "pulse_id": pulse_id,
                "pulse_name": (
                    str(row["pulse_name"]) if row["pulse_name"] is not None else None
                ),
                "pulse_created": _iso8601(row["pulse_created"]),
                "fetched_at": _iso8601(row["fetched_at"]),
                "indicator_count": int(row["indicator_count"]),
                "indicator_types": indicator_types,
                "distinct_type_count": int(row["distinct_type_count"]),
                "source": str(row["source"]) if row["source"] is not None else None,
            }
        )

    return {"count": len(pulses), "pulses": pulses}


compute_pulse_overview = get_pulse_overview
list_pulse_overview = get_pulse_overview
build_pulse_overview = get_pulse_overview


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value="192.0.2.10",
                    pulse_id="pulse-live",
                    pulse_name="Live pulse",
                    pulse_created=datetime(2025, 1, 2, 3, 4, 5),
                    is_aggregator=False,
                    source="example",
                    source_url="https://example.test/pulse-live",
                    fetched_at=datetime(2025, 1, 2, 4, 0, 0),
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-live",
                    pulse_name="Live pulse",
                    pulse_created=datetime(2025, 1, 2, 3, 4, 5),
                    is_aggregator=False,
                    source="example",
                    source_url="https://example.test/pulse-live",
                    fetched_at=datetime(2025, 1, 2, 4, 0, 0),
                ),
                ThreatIntelRef(
                    indicator_type="ipv4",
                    indicator_value="198.51.100.20",
                    pulse_id="pulse-aggregator",
                    pulse_name="Aggregator pulse",
                    pulse_created=datetime(2025, 1, 3, 3, 4, 5),
                    is_aggregator=True,
                    source="example",
                    source_url="https://example.test/pulse-aggregator",
                    fetched_at=datetime(2025, 1, 3, 4, 0, 0),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/threat-intel/pulses")
    def threat_intel_pulses(
        session: Session = Depends(get_session),
        limit: int = 50,
        include_aggregators: bool = False,
    ):
        return get_pulse_overview(
            session=session,
            limit=limit,
            include_aggregators=include_aggregators,
        )

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/pulses")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 1
        pulse = payload["pulses"][0]
        assert pulse["pulse_id"] == "pulse-live"
        assert pulse["indicator_count"] == 2
        assert pulse["distinct_type_count"] == 2
        assert pulse["indicator_types"] == ["domain", "ipv4"]

    print("PASS")