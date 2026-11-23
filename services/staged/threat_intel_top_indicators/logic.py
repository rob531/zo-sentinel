"""Logic for threat intelligence top-indicator aggregation."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.models import ThreatIntelRef


def get_top_indicators(
    db: Session,
    top_n: int = 10,
    limit: Optional[int] = None,
) -> Dict[str, List[Dict[str, object]]]:
    """Return the most frequent indicator values grouped by indicator type."""
    if limit is not None:
        top_n = limit
    if top_n < 1:
        return {"types": []}

    rows = db.execute(
        select(
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.indicator_value,
            func.count(),
            func.count(distinct(ThreatIntelRef.pulse_name)),
            func.max(ThreatIntelRef.pulse_created),
            func.sum(
                case(
                    (ThreatIntelRef.is_aggregator.is_(True), 1),
                    else_=0,
                )
            ),
        )
        .group_by(
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.indicator_value,
        )
        .order_by(
            ThreatIntelRef.indicator_type,
            func.count().desc(),
            ThreatIntelRef.indicator_value,
        )
    ).all()

    grouped: Dict[str, Dict[str, object]] = {}
    for (
        indicator_type,
        indicator_value,
        value_count,
        _distinct_pulses,
        latest_pulse_created,
        aggregator_count,
    ) in rows:
        type_key = indicator_type or "unknown"
        entry = grouped.setdefault(
            type_key,
            {
                "indicator_type": indicator_type or "unknown",
                "count": 0,
                "distinct_values": 0,
                "top_values": [],
                "latest_pulse_created": None,
                "is_aggregator_count": 0,
            },
        )
        entry["count"] = int(entry["count"]) + int(value_count)
        entry["distinct_values"] = int(entry["distinct_values"]) + 1
        entry["is_aggregator_count"] = int(entry["is_aggregator_count"]) + int(
            aggregator_count or 0
        )
        if latest_pulse_created is not None:
            current_latest = entry["latest_pulse_created"]
            if current_latest is None or latest_pulse_created > current_latest:
                entry["latest_pulse_created"] = latest_pulse_created
        top_values = entry["top_values"]
        assert isinstance(top_values, list)
        if len(top_values) < top_n:
            top_values.append(
                {
                    "value": indicator_value,
                    "count": int(value_count),
                }
            )

    return {"types": list(grouped.values())}


def top_indicators(
    db: Session,
    top_n: int = 10,
    limit: Optional[int] = None,
) -> Dict[str, List[Dict[str, object]]]:
    """Compatibility name for the top-indicator query."""
    return get_top_indicators(db, top_n=top_n, limit=limit)


def get_threat_intel_top_indicators(
    db: Session,
    top_n: int = 10,
    limit: Optional[int] = None,
) -> Dict[str, List[Dict[str, object]]]:
    """Compatibility name for the top-indicator query."""
    return get_top_indicators(db, top_n=top_n, limit=limit)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/threat-intel/top-indicators")
    def top_indicators_endpoint(db: Session = Depends(get_session)):
        return get_top_indicators(db)

    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.utcnow()
    with TestSession() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_name="pulse-one",
                    pulse_created=now - timedelta(days=2),
                    pulse_id="pulse-1",
                    source="internal",
                    source_url="https://example.invalid/one",
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_name="pulse-two",
                    pulse_created=now - timedelta(days=1),
                    pulse_id="pulse-2",
                    source="internal",
                    source_url="https://example.invalid/two",
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="domain",
                    indicator_value="example.invalid",
                    pulse_name="pulse-three",
                    pulse_created=now - timedelta(days=3),
                    pulse_id="pulse-3",
                    source="aggregator",
                    source_url="https://example.invalid/three",
                ),
                ThreatIntelRef(
                    id=4,
                    indicator_type="domain",
                    indicator_value="other.invalid",
                    pulse_name="pulse-four",
                    pulse_created=now - timedelta(days=4),
                    pulse_id="pulse-4",
                    source="aggregator",
                    source_url="https://example.invalid/four",
                ),
            ]
        )
        session.commit()

    response = TestClient(test_app).get("/api/threat-intel/top-indicators")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["types"]) == 2
    by_type = {item["indicator_type"]: item for item in payload["types"]}
    assert by_type["ip"]["count"] == 2
    assert by_type["domain"]["count"] == 2
    assert by_type["ip"]["top_values"][0]["value"] == "192.0.2.1"
    assert by_type["ip"]["top_values"][0]["count"] == 2
    print("PASS")