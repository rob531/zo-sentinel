from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session, aliased

from app.db import get_session
from app.models import ThreatIntelRef


def get_indicator_type_rollup(
    db: Session = Depends(get_session),
    days: int = 30,
) -> Dict[str, Any]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    latest_ref = aliased(ThreatIntelRef)
    latest_source_url = (
        select(latest_ref.source_url)
        .where(
            latest_ref.indicator_type == ThreatIntelRef.indicator_type,
            latest_ref.pulse_created >= cutoff,
        )
        .order_by(
            latest_ref.pulse_created.desc(),
            latest_ref.id.desc(),
        )
        .limit(1)
        .correlate(ThreatIntelRef)
        .scalar_subquery()
    )

    query = (
        select(
            ThreatIntelRef.indicator_type,
            func.count(ThreatIntelRef.id).label("count"),
            func.count(distinct(ThreatIntelRef.indicator_value)).label("distinct_values"),
            func.coalesce(
                func.sum(
                    case(
                        (ThreatIntelRef.is_aggregator.is_(True), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("aggregator_count"),
            func.max(ThreatIntelRef.pulse_created).label("latest_pulse_created"),
            latest_source_url.label("latest_source_url"),
        )
        .where(ThreatIntelRef.pulse_created >= cutoff)
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(ThreatIntelRef.indicator_type)
    )

    rows = db.execute(query).mappings().all()

    types: List[Dict[str, Any]] = [
        {
            "indicator_type": row["indicator_type"],
            "count": int(row["count"] or 0),
            "distinct_values": int(row["distinct_values"] or 0),
            "aggregator_count": int(row["aggregator_count"] or 0),
            "latest_pulse_created": row["latest_pulse_created"],
            "latest_source_url": row["latest_source_url"],
        }
        for row in rows
    ]

    return {
        "days": days,
        "total": sum(item["count"] for item in types),
        "types": types,
    }


def get_indicator_types(
    db: Session = Depends(get_session),
    days: int = 30,
) -> Dict[str, Any]:
    return get_indicator_type_rollup(db=db, days=days)


def rollup_indicator_types(
    db: Session = Depends(get_session),
    days: int = 30,
) -> Dict[str, Any]:
    return get_indicator_type_rollup(db=db, days=days)


if __name__ == "__main__":
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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session = TestSession()
    session.add_all(
        [
            ThreatIntelRef(
                id=1,
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id=10,
                pulse_name="recent",
                pulse_created=now,
                is_aggregator=False,
                source="test",
                source_url="https://example.test/recent-ip",
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="ip",
                indicator_value="192.0.2.2",
                pulse_id=10,
                pulse_name="recent",
                pulse_created=now,
                is_aggregator=True,
                source="test",
                source_url="https://example.test/recent-ip-2",
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id=11,
                pulse_name="earlier",
                pulse_created=now - timedelta(days=5),
                is_aggregator=False,
                source="test",
                source_url="https://example.test/domain",
                fetched_at=now,
            ),
        ]
    )
    session.commit()

    def override_get_session():
        test_session = TestSession()
        try:
            yield test_session
        finally:
            test_session.close()

    test_app = FastAPI()

    @test_app.get("/api/threat-intel/indicator-types")
    def indicator_types_endpoint(
        days: int = 30,
        db: Session = Depends(get_session),
    ):
        return get_indicator_type_rollup(db=db, days=days)

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/threat-intel/indicator-types?days=30")
    assert response.status_code == 200
    payload = response.json()
    assert payload["days"] == 30
    assert payload["total"] == 3
    assert len(payload["types"]) == 2
    assert next(item for item in payload["types"] if item["indicator_type"] == "ip")["count"] == 2
    print("PASS")