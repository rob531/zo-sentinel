"""logic.py -- threat intelligence indicator catalog data/computation layer."""

from __future__ import annotations

from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_indicator_catalog(
    db: Session,
    source: Optional[str] = None,
    indicator_type: Optional[str] = None,
) -> Dict[str, object]:
    """Build the threat intelligence indicator catalog from real app data."""
    conditions = []
    if source is not None:
        conditions.append(ThreatIntelRef.source == source)
    if indicator_type is not None:
        conditions.append(ThreatIntelRef.indicator_type == indicator_type)

    statement = select(ThreatIntelRef)
    if conditions:
        statement = statement.where(*conditions)
    statement = statement.order_by(ThreatIntelRef.fetched_at.desc())

    rows = list(db.execute(statement).scalars())

    type_counts: Dict[str, int] = {}
    source_counts: Dict[str, int] = {}
    pulse_rows: Dict[object, Dict[str, object]] = {}
    aggregator_count = 0

    for row in rows:
        current_type = row.indicator_type
        type_counts[current_type] = type_counts.get(current_type, 0) + 1

        current_source = row.source
        source_counts[current_source] = source_counts.get(current_source, 0) + 1

        if bool(row.is_aggregator):
            aggregator_count += 1

        pulse = pulse_rows.get(row.pulse_id)
        if pulse is None:
            pulse = {
                "pulse_id": row.pulse_id,
                "pulse_name": row.pulse_name,
                "indicator_count": 0,
                "newest_fetched_at": row.fetched_at,
            }
            pulse_rows[row.pulse_id] = pulse
        pulse["indicator_count"] = int(pulse["indicator_count"]) + 1
        if row.fetched_at is not None:
            newest = pulse["newest_fetched_at"]
            if newest is None or row.fetched_at > newest:
                pulse["newest_fetched_at"] = row.fetched_at

    by_type = [
        {"indicator_type": key, "count": count}
        for key, count in sorted(type_counts.items(), key=lambda item: item[0])
    ]
    by_source = [
        {"source": key, "count": count}
        for key, count in sorted(source_counts.items(), key=lambda item: item[0])
    ]
    pulses = [pulse_rows[key] for key in sorted(pulse_rows, key=lambda value: str(value))]

    recent = [
        {
            "indicator_type": row.indicator_type,
            "indicator_value": row.indicator_value,
            "source": row.source,
            "fetched_at": row.fetched_at,
        }
        for row in rows[:25]
    ]

    return {
        "total": len(rows),
        "by_type": by_type,
        "by_source": by_source,
        "aggregator_count": aggregator_count,
        "pulses": pulses,
        "recent": recent,
    }


def get_threat_intel_indicator_catalog(
    db: Session,
    source: Optional[str] = None,
    indicator_type: Optional[str] = None,
) -> Dict[str, object]:
    return get_indicator_catalog(db, source=source, indicator_type=indicator_type)


def get_threat_intel_indicators(
    db: Session,
    source: Optional[str] = None,
    indicator_type: Optional[str] = None,
) -> Dict[str, object]:
    return get_indicator_catalog(db, source=source, indicator_type=indicator_type)


def _self_test() -> None:
    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/threat-intel/indicators")
    def indicators(
        db: Session = Depends(get_session),
        source: Optional[str] = None,
        indicator_type: Optional[str] = None,
    ):
        return get_indicator_catalog(db, source=source, indicator_type=indicator_type)

    with session_factory() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id=1,
                    pulse_name="pulse-one",
                    is_aggregator=True,
                    source="alpha",
                    source_url="https://alpha.example/1",
                ),
                ThreatIntelRef(
                    indicator_type="domain",
                    indicator_value="example.test",
                    pulse_id=1,
                    pulse_name="pulse-one",
                    is_aggregator=False,
                    source="alpha",
                    source_url="https://alpha.example/2",
                ),
                ThreatIntelRef(
                    indicator_type="ip",
                    indicator_value="198.51.100.2",
                    pulse_id=2,
                    pulse_name="pulse-two",
                    is_aggregator=False,
                    source="beta",
                    source_url="https://beta.example/1",
                ),
            ]
        )
        session.commit()

        result = indicators(db=session)
        assert result["total"] == 3
        assert len(result["by_type"]) == 2
        assert sum(item["count"] for item in result["by_type"]) == 3
        assert result["aggregator_count"] == 1

        filtered = indicators(db=session, source="alpha")
        assert filtered["total"] == 2
        assert filtered["by_source"] == [{"source": "alpha", "count": 2}]

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()