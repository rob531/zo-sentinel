"""logic.py -- the service's data/computation layer (one concern, one file)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_ref_source_mix(db: Session) -> Dict[str, Any]:
    """Summarise threat-intel indicator references by source and indicator type."""
    total = int(
        db.execute(select(func.count()).select_from(ThreatIntelRef)).scalar() or 0
    )

    source_rows = db.execute(
        select(
            ThreatIntelRef.source,
            func.count(),
            func.sum(
                case(
                    (ThreatIntelRef.is_aggregator.is_(True), 1),
                    else_=0,
                )
            ),
            func.max(ThreatIntelRef.fetched_at),
        )
        .group_by(ThreatIntelRef.source)
        .order_by(ThreatIntelRef.source)
    ).all()

    by_source: List[Dict[str, Any]] = [
        {
            "source": source,
            "count": int(count),
            "aggregator_count": int(aggregator_count or 0),
            "latest_fetched_at": latest_fetched_at,
        }
        for source, count, aggregator_count, latest_fetched_at in source_rows
    ]

    indicator_rows = db.execute(
        select(ThreatIntelRef.indicator_type, func.count())
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(ThreatIntelRef.indicator_type)
    ).all()

    by_indicator_type: List[Dict[str, Any]] = [
        {"indicator_type": indicator_type, "count": int(count)}
        for indicator_type, count in indicator_rows
    ]

    return {
        "generated_at": datetime.now(timezone.utc),
        "total": total,
        "by_source": by_source,
        "by_indicator_type": by_indicator_type,
    }


def ref_source_mix(db: Session) -> Dict[str, Any]:
    """Compatibility alias for the reference source mix computation."""
    return get_ref_source_mix(db)


def get_source_mix(db: Session) -> Dict[str, Any]:
    """Compatibility alias for the reference source mix computation."""
    return get_ref_source_mix(db)


def _self_test() -> None:
    from fastapi import Depends, FastAPI
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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = TestingSession()
    db.add_all(
        [
            ThreatIntelRef(
                indicator_type="ip",
                indicator_value="192.0.2.1",
                is_aggregator=False,
                pulse_name="pulse-a",
                pulse_id="pulse-a",
                source="feed-a",
                source_url="https://feed-a.example/1",
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.test",
                is_aggregator=True,
                pulse_name="pulse-b",
                pulse_id="pulse-b",
                source="feed-b",
                source_url="https://feed-b.example/1",
            ),
            ThreatIntelRef(
                indicator_type="domain",
                indicator_value="example.org",
                is_aggregator=False,
                pulse_name="pulse-b",
                pulse_id="pulse-b",
                source="feed-a",
                source_url="https://feed-a.example/2",
            ),
        ]
    )
    db.commit()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threat/ref-source-mix")
    def ref_source_mix_route(db: Session = Depends(get_session)):
        return get_ref_source_mix(db)

    response = TestClient(that_app).get("/api/threat/ref-source-mix")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert len(payload["by_source"]) == 2
    aggregator_source = next(
        item for item in payload["by_source"] if item["source"] == "feed-b"
    )
    assert aggregator_source["aggregator_count"] == 1
    assert len(payload["by_indicator_type"]) == 2
    print("PASS")


if __name__ == "__main__":
    _self_test()