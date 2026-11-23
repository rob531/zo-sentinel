"""Data and computation layer for the threat intelligence top-indicators service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _pulse_age_days(value: Any) -> int | None:
    """Return the age of a pulse timestamp in whole days."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return max(0, (datetime.now(timezone.utc) - value).days)


def get_top_indicators(db: Session, top_n: int = 10) -> Dict[str, List[Dict[str, Any]]]:
    """Return the most frequently observed indicators for each indicator type.

    The count for a type is the number of references in that type.  Counts
    for individual values are the number of references containing that value.
    Distinct values are counted independently from references so repeated
    observations do not inflate the distinct-value metric.
    """
    top_n = max(1, int(top_n))
    rows = db.execute(
        select(
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.indicator_value,
            func.count().label("reference_count"),
            func.count(func.distinct(ThreatIntelRef.pulse_id)).label("pulse_count"),
            func.max(ThreatIntelRef.pulse_created).label("latest_pulse_created"),
            func.sum(
                case((ThreatIntelRef.is_aggregator.is_(True), 1), else_=0)
            ).label("aggregator_count"),
        )
        .group_by(
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.indicator_value,
        )
    ).all()

    grouped: Dict[str, List[Any]] = {}
    for row in rows:
        grouped.setdefault(row.indicator_type, []).append(row)

    types: List[Dict[str, Any]] = []
    for indicator_type, indicator_rows in grouped.items():
        indicator_rows.sort(
            key=lambda row: (-int(row.reference_count), str(row.indicator_value))
        )
        latest_pulse_created = max(
            (row.latest_pulse_created for row in indicator_rows if row.latest_pulse_created is not None),
            default=None,
        )
        types.append(
            {
                "indicator_type": indicator_type,
                "count": sum(int(row.reference_count) for row in indicator_rows),
                "distinct_values": len({row.indicator_value for row in indicator_rows}),
                "top_values": [
                    {
                        "value": row.indicator_value,
                        "count": int(row.reference_count),
                    }
                    for row in indicator_rows[:top_n]
                ],
                "latest_pulse_created": latest_pulse_created,
                "is_aggregator_count": sum(
                    int(row.aggregator_count or 0) for row in indicator_rows
                ),
            }
        )

    types.sort(key=lambda item: (-item["count"], str(item["indicator_type"])))
    return {"types": types}


def top_indicators(db: Session, top_n: int = 10) -> Dict[str, List[Dict[str, Any]]]:
    """Compatibility alias for callers using the resource name directly."""
    return get_top_indicators(db, top_n=top_n)


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
    ThreatIntelRef.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/threat-intel/top-indicators")
    def test_route(db: Session = Depends(get_session)):
        return get_top_indicators(db, top_n=10)

    now = datetime.now(timezone.utc) - timedelta(days=2)
    test_session = TestSession()
    test_session.add_all(
        [
            ThreatIntelRef(
                id=1,
                indicator_type="ip",
                indicator_value="192.0.2.10",
                pulse_id="pulse-1",
                pulse_name="Pulse 1",
                pulse_created=now,
                source="test",
                source_url="https://example.test/pulse-1",
                is_aggregator=False,
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="ip",
                indicator_value="192.0.2.10",
                pulse_id="pulse-2",
                pulse_name="Pulse 2",
                pulse_created=now - timedelta(days=1),
                source="test",
                source_url="https://example.test/pulse-2",
                is_aggregator=True,
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="ip",
                indicator_value="192.0.2.11",
                pulse_id="pulse-3",
                pulse_name="Pulse 3",
                pulse_created=now - timedelta(days=1),
                source="test",
                source_url="https://example.test/pulse-3",
                is_aggregator=False,
            ),
            ThreatIntelRef(
                id=4,
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id="pulse-4",
                pulse_name="Pulse 4",
                pulse_created=now,
                source="test",
                source_url="https://example.test/pulse-4",
                is_aggregator=False,
            ),
        ]
    )
    test_session.commit()
    test_session.close()

    response = TestClient(test_app).get("/api/threat-intel/top-indicators")
    assert response.status_code == 200, response.text
    payload = response.json()
    type_rows = {row["indicator_type"]: row for row in payload["types"]}
    assert type_rows["ip"]["count"] == 3
    assert type_rows["domain"]["count"] == 1
    assert type_rows["ip"]["top_values"][0]["value"] == "192.0.2.10"
    assert type_rows["ip"]["top_values"][0]["count"] == 2
    print("PASS")