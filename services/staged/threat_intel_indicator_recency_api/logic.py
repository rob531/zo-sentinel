from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import case, desc, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _age_days(value: Any, now: datetime) -> int | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0, (now - value).days)


def get_indicator_recency(session: Session = Depends(get_session)) -> dict[str, Any]:
    now = datetime.now(timezone.utc)

    rows = session.execute(
        select(
            ThreatIntelRef.indicator_type.label("indicator_type"),
            func.count(ThreatIntelRef.id).label("count"),
            func.coalesce(
                func.sum(
                    case(
                        (ThreatIntelRef.is_aggregator.is_(True), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("aggregator_count"),
            func.max(ThreatIntelRef.pulse_created).label("latest_pulse"),
            func.max(ThreatIntelRef.fetched_at).label("last_fetch"),
        )
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(ThreatIntelRef.indicator_type)
    ).all()

    types = []
    for row in rows:
        types.append(
            {
                "indicator_type": row.indicator_type,
                "count": int(row.count or 0),
                "aggregator_count": int(row.aggregator_count or 0),
                "latest_pulse": _as_iso(row.latest_pulse),
                "last_fetch": _as_iso(row.last_fetch),
                "age_days": _age_days(row.latest_pulse, now),
            }
        )

    recent_rows = session.execute(
        select(
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.indicator_value,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.pulse_created,
        )
        .where(ThreatIntelRef.pulse_created.is_not(None))
        .order_by(
            ThreatIntelRef.pulse_created.is_(None),
            desc(ThreatIntelRef.pulse_created),
        )
        .limit(20)
    ).all()

    recent = [
        {
            "indicator_type": row.indicator_type,
            "indicator_value": row.indicator_value,
            "pulse_name": row.pulse_name,
            "pulse_created": _as_iso(row.pulse_created),
        }
        for row in recent_rows
    ]

    return {
        "generated_at": now.isoformat(),
        "types": types,
        "recent": recent,
    }


def get_stats(session: Session = Depends(get_session)) -> dict[str, Any]:
    return get_indicator_recency(session)


if __name__ == "__main__":
    from fastapi import FastAPI
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

    with TestSession() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.10",
                    is_aggregator=True,
                    pulse_created=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    fetched_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    pulse_id="pulse-1",
                    pulse_name="Pulse One",
                    source="test",
                    source_url="https://example.invalid/1",
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.11",
                    is_aggregator=False,
                    pulse_created=datetime(2024, 1, 3, tzinfo=timezone.utc),
                    fetched_at=datetime(2024, 1, 4, tzinfo=timezone.utc),
                    pulse_id="pulse-2",
                    pulse_name="Pulse Two",
                    source="test",
                    source_url="https://example.invalid/2",
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.invalid",
                    is_aggregator=False,
                    pulse_created=datetime(2024, 1, 5, tzinfo=timezone.utc),
                    fetched_at=datetime(2024, 1, 6, tzinfo=timezone.utc),
                    pulse_id="pulse-3",
                    pulse_name="Pulse Three",
                    source="test",
                    source_url="https://example.invalid/3",
                ),
            ]
        )
        session.commit()

    def override_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_session

    @that_app.get("/api/threat-intel/indicator-recency")
    def indicator_recency(session: Session = Depends(get_session)):
        return get_indicator_recency(session)

    response = TestClient(that_app).get("/api/threat-intel/indicator-recency")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["types"]) == 2
    ip_type = next(item for item in payload["types"] if item["indicator_type"] == "ip")
    assert ip_type["aggregator_count"] == 1
    print("PASS")