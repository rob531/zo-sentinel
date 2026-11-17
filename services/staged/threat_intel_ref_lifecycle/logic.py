from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from fastapi import Depends, FastAPI, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def get_lifecycle_data(db: Session, days: int = 30) -> Dict[str, object]:
    """Return threat-intel reference counts grouped by type and source."""
    window_days = max(0, int(days))
    cutoff = datetime.utcnow() - timedelta(days=window_days)

    stale_id = case(
        (ThreatIntelRef.fetched_at < cutoff, ThreatIntelRef.id),
        else_=None,
    )

    rows = db.execute(
        select(
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.source,
            func.count(func.distinct(ThreatIntelRef.id)).label("count"),
            func.count(func.distinct(stale_id)).label("stale"),
        )
        .group_by(ThreatIntelRef.indicator_type, ThreatIntelRef.source)
        .order_by(ThreatIntelRef.indicator_type, ThreatIntelRef.source)
    ).all()

    by_indicator_type: Dict[str, Dict[str, int]] = {}
    by_source: Dict[str, int] = {}
    total = 0
    stale_total = 0

    for indicator_type, source, count, stale in rows:
        type_key = indicator_type or "unknown"
        source_key = source or "unknown"
        type_count = int(count or 0)
        type_stale = int(stale or 0)

        if type_key not in by_indicator_type:
            by_indicator_type[type_key] = {"count": 0, "stale": 0}
        by_indicator_type[type_key]["count"] += type_count
        by_indicator_type[type_key]["stale"] += type_stale

        by_source[source_key] = by_source.get(source_key, 0) + type_count
        total += type_count
        stale_total += type_stale

    return {
        "days": window_days,
        "by_indicator_type": [
            {
                "indicator_type": indicator_type,
                "count": values["count"],
                "stale": values["stale"],
            }
            for indicator_type, values in sorted(by_indicator_type.items())
        ],
        "by_source": [
            {"source": source, "count": count}
            for source, count in sorted(by_source.items())
        ],
        "total": total,
        "stale_total": stale_total,
    }


def get_threat_intel_ref_lifecycle(db: Session, days: int = 30) -> Dict[str, object]:
    return get_lifecycle_data(db, days)


def get_ref_lifecycle(db: Session, days: int = 30) -> Dict[str, object]:
    return get_lifecycle_data(db, days)


def calculate_lifecycle(db: Session, days: int = 30) -> Dict[str, object]:
    return get_lifecycle_data(db, days)


def lifecycle_data(db: Session, days: int = 30) -> Dict[str, object]:
    return get_lifecycle_data(db, days)


def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.utcnow()
    with SessionLocal() as session:
        session.add_all(
            [
                ThreatIntelRef(
                    id=1,
                    indicator_type="ip",
                    indicator_value="192.0.2.1",
                    pulse_id="pulse-1",
                    pulse_name="recent pulse",
                    pulse_created=now,
                    is_aggregator=False,
                    source="seed-source",
                    source_url="https://example.invalid/recent",
                    fetched_at=now,
                ),
                ThreatIntelRef(
                    id=2,
                    indicator_type="domain",
                    indicator_value="example.invalid",
                    pulse_id="pulse-2",
                    pulse_name="stale pulse",
                    pulse_created=now - timedelta(days=100),
                    is_aggregator=False,
                    source="seed-source",
                    source_url="https://example.invalid/stale",
                    fetched_at=now - timedelta(days=100),
                ),
                ThreatIntelRef(
                    id=3,
                    indicator_type="aggregator",
                    indicator_value="aggregated-ref",
                    pulse_id="pulse-3",
                    pulse_name="aggregator pulse",
                    pulse_created=now,
                    is_aggregator=True,
                    source="seed-source",
                    source_url="https://example.invalid/aggregator",
                    fetched_at=now,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/threat-intel/refs/lifecycle")
    def lifecycle(
        days: int = Query(30),
        db: Session = Depends(get_session),
    ):
        return get_lifecycle_data(db, days)

    def override_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    from fastapi.testclient import TestClient

    with TestClient(test_app) as client:
        response = client.get(
            "/api/threat-intel/refs/lifecycle",
            params={"days": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert payload["stale_total"] >= 1
        assert any(
            row["indicator_type"] == "ip"
            for row in payload["by_indicator_type"]
        )

    print("PASS")


if __name__ == "__main__":
    _run_self_test()