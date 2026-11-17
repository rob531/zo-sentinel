"""logic.py -- threat intelligence pulse aggregation logic.

Reads the real threat_intel_refs table through the application SQLAlchemy
session and computes pulse-level indicator summaries.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session  # noqa: F401
from app.models import ThreatIntelRef


def _normalize_limit(limit: Optional[int]) -> int:
    if limit is None:
        return 50
    return max(1, min(int(limit), 200))


def _apply_indicator_filter(statement, indicator_type: Optional[str]):
    if indicator_type:
        statement = statement.where(ThreatIntelRef.indicator_type == indicator_type)
    return statement


def get_threat_intel_pulses(
    db: Session,
    limit: int = 50,
    indicator_type: Optional[str] = None,
) -> List[Dict]:
    """Return pulse summaries ordered by descending indicator count.

    ``indicator_type`` limits the source rows used for the aggregation.  The
    returned ``indicator_types`` mapping contains counts for the filtered
    rows, including the requested type when one was supplied.
    """
    limit = _normalize_limit(limit)

    count_statement = _apply_indicator_filter(
        select(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            func.count(ThreatIntelRef.id).label("indicator_count"),
            func.min(ThreatIntelRef.pulse_created).label("first_seen"),
            func.max(ThreatIntelRef.pulse_created).label("last_seen"),
            func.max(ThreatIntelRef.fetched_at).label("latest_fetched_at"),
        ),
        indicator_type,
    )
    count_statement = (
        count_statement.group_by(ThreatIntelRef.pulse_id, ThreatIntelRef.pulse_name)
        .order_by(
            func.count(ThreatIntelRef.id).desc(),
            ThreatIntelRef.pulse_id.asc(),
        )
        .limit(limit)
    )
    grouped_rows = db.execute(count_statement).all()

    pulses: List[Dict] = []
    for row in grouped_rows:
        pulses.append(
            {
                "pulse_id": row.pulse_id,
                "pulse_name": row.pulse_name,
                "indicator_count": int(row.indicator_count),
                "indicator_types": {},
                "first_seen": row.first_seen,
                "last_seen": row.last_seen,
                "latest_fetched_at": row.latest_fetched_at,
                "is_aggregator": False,
                "source": None,
                "source_url": None,
            }
        )

    if not pulses:
        return pulses

    pulse_ids = [pulse["pulse_id"] for pulse in pulses]
    type_statement = _apply_indicator_filter(
        select(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.indicator_type,
            func.count(ThreatIntelRef.id).label("indicator_count"),
        ).where(ThreatIntelRef.pulse_id.in_(pulse_ids)),
        indicator_type,
    )
    type_statement = type_statement.group_by(
        ThreatIntelRef.pulse_id,
        ThreatIntelRef.indicator_type,
    )
    type_rows = db.execute(type_statement).all()
    counts_by_pulse: Dict[object, Dict[str, int]] = {}
    for row in type_rows:
        counts_by_pulse.setdefault(row.pulse_id, {})[
            row.indicator_type
        ] = int(row.indicator_count)

    metadata_rows = db.execute(
        select(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.is_aggregator,
            ThreatIntelRef.source,
            ThreatIntelRef.source_url,
        ).where(ThreatIntelRef.pulse_id.in_(pulse_ids))
    ).all()
    metadata_by_pulse: Dict[object, Dict] = {}
    for row in metadata_rows:
        metadata_by_pulse.setdefault(
            row.pulse_id,
            {
                "is_aggregator": bool(row.is_aggregator),
                "source": row.source,
                "source_url": row.source_url,
            },
        )

    for pulse in pulses:
        pulse["indicator_types"] = counts_by_pulse.get(pulse["pulse_id"], {})
        pulse.update(metadata_by_pulse.get(pulse["pulse_id"], {}))

    return pulses


def list_threat_intel_pulses(
    db: Session,
    limit: int = 50,
    indicator_type: Optional[str] = None,
) -> List[Dict]:
    """Compatibility alias for :func:`get_threat_intel_pulses`."""
    return get_threat_intel_pulses(db, limit=limit, indicator_type=indicator_type)


def get_pulses(
    db: Session,
    limit: int = 50,
    indicator_type: Optional[str] = None,
) -> List[Dict]:
    """Short compatibility alias for :func:`get_threat_intel_pulses`."""
    return get_threat_intel_pulses(db, limit=limit, indicator_type=indicator_type)


def count_threat_intel_pulses(
    db: Session,
    indicator_type: Optional[str] = None,
) -> int:
    """Count distinct pulses matching the optional indicator filter."""
    statement = _apply_indicator_filter(
        select(func.count(func.distinct(ThreatIntelRef.pulse_id))),
        indicator_type,
    )
    return int(db.execute(statement).scalar_one())


if __name__ == "__main__":
    from fastapi import FastAPI
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
    TestingSession = sessionmaker(bind=engine)

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()

    @test_app.get("/api/threat-intel/pulses")
    def threat_intel_pulses(limit: int = 50, indicator_type: Optional[str] = None):
        db = TestingSession()
        try:
            pulses = get_threat_intel_pulses(
                db, limit=limit, indicator_type=indicator_type
            )
            return {
                "pulses": pulses,
                "total": count_threat_intel_pulses(db, indicator_type=indicator_type),
                "limit": _normalize_limit(limit),
            }
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    session = TestingSession()
    try:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-a",
                    pulse_name="Pulse A",
                    pulse_created=datetime(2024, 1, 1),
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/a",
                    fetched_at=datetime(2024, 1, 2),
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id="pulse-a",
                    pulse_name="Pulse A",
                    pulse_created=datetime(2024, 1, 2),
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/a",
                    fetched_at=datetime(2024, 1, 3),
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.2",
                    pulse_id="pulse-a",
                    pulse_name="Pulse A",
                    pulse_created=datetime(2024, 1, 3),
                    is_aggregator=False,
                    source="test",
                    source_url="https://example.test/a",
                    fetched_at=datetime(2024, 1, 4),
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.3",
                    pulse_id="pulse-b",
                    pulse_name="Pulse B",
                    pulse_created=datetime(2024, 1, 1),
                    is_aggregator=True,
                    source="test",
                    source_url="https://example.test/b",
                    fetched_at=datetime(2024, 1, 5),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    response = TestClient(test_app).get("/api/threat-intel/pulses")
    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 2
    assert body["pulses"][0]["indicator_count"] == 3
    print("PASS")