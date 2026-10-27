from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


def _iso_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def get_aggregator_flag_breakdown(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    indicator_rows = db.execute(
        select(
            ThreatIntelRef.indicator_type.label("indicator_type"),
            func.count(ThreatIntelRef.id).label("total"),
            func.sum(
                case(
                    (ThreatIntelRef.is_aggregator.is_(True), 1),
                    else_=0,
                )
            ).label("aggregator"),
        )
        .group_by(ThreatIntelRef.indicator_type)
        .order_by(ThreatIntelRef.indicator_type)
    ).all()

    source_rows = db.execute(
        select(
            ThreatIntelRef.source.label("source"),
            func.count(ThreatIntelRef.id).label("total"),
        )
        .group_by(ThreatIntelRef.source)
        .order_by(ThreatIntelRef.source)
    ).all()

    totals = db.execute(
        select(
            func.sum(
                case(
                    (ThreatIntelRef.is_aggregator.is_(True), 1),
                    else_=0,
                )
            ).label("aggregator_rows"),
            func.sum(
                case(
                    (ThreatIntelRef.is_aggregator.is_(False), 1),
                    else_=0,
                )
            ).label("direct_rows"),
            func.count(func.distinct(ThreatIntelRef.pulse_id)).label(
                "distinct_pulses"
            ),
            func.min(ThreatIntelRef.pulse_created).label("oldest_pulse"),
            func.max(ThreatIntelRef.pulse_created).label("newest_pulse"),
        )
    ).one()

    by_indicator_type: List[Dict[str, Any]] = [
        {
            "indicator_type": row.indicator_type or "unknown",
            "total": int(row.total or 0),
            "aggregator": int(row.aggregator or 0),
        }
        for row in indicator_rows
    ]

    by_source: List[Dict[str, Any]] = [
        {
            "source": row.source or "unknown",
            "total": int(row.total or 0),
        }
        for row in source_rows
    ]

    return {
        "aggregator_rows": int(totals.aggregator_rows or 0),
        "direct_rows": int(totals.direct_rows or 0),
        "by_indicator_type": by_indicator_type,
        "by_source": by_source,
        "distinct_pulses": int(totals.distinct_pulses or 0),
        "oldest_pulse": _iso_timestamp(totals.oldest_pulse),
        "newest_pulse": _iso_timestamp(totals.newest_pulse),
    }


def get_aggregator_breakdown(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_aggregator_flag_breakdown(db)


def get_breakdown(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_aggregator_flag_breakdown(db)


def compute_aggregator_breakdown(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_aggregator_flag_breakdown(db)


def _run_self_test() -> None:
    from datetime import datetime, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": 1,
            "indicator_type": "ip",
            "indicator_value": "192.0.2.1",
            "pulse_id": "pulse-1",
            "pulse_name": "Pulse One",
            "pulse_created": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "is_aggregator": True,
            "source": "alpha",
            "source_url": "https://example.test/1",
            "fetched_at": now,
        },
        {
            "id": 2,
            "indicator_type": "domain",
            "indicator_value": "example.test",
            "pulse_id": "pulse-2",
            "pulse_name": "Pulse Two",
            "pulse_created": datetime(2024, 1, 2, tzinfo=timezone.utc),
            "is_aggregator": True,
            "source": "alpha",
            "source_url": "https://example.test/2",
            "fetched_at": now,
        },
        {
            "id": 3,
            "indicator_type": "ip",
            "indicator_value": "192.0.2.2",
            "pulse_id": "pulse-3",
            "pulse_name": "Pulse Three",
            "pulse_created": datetime(2024, 1, 3, tzinfo=timezone.utc),
            "is_aggregator": False,
            "source": "beta",
            "source_url": "https://example.test/3",
            "fetched_at": now,
        },
        {
            "id": 4,
            "indicator_type": "ip",
            "indicator_value": "192.0.2.3",
            "pulse_id": "pulse-4",
            "pulse_name": "Pulse Four",
            "pulse_created": datetime(2024, 1, 4, tzinfo=timezone.utc),
            "is_aggregator": False,
            "source": "beta",
            "source_url": "https://example.test/4",
            "fetched_at": now,
        },
        {
            "id": 5,
            "indicator_type": "domain",
            "indicator_value": "direct.test",
            "pulse_id": "pulse-4",
            "pulse_name": "Pulse Four",
            "pulse_created": datetime(2024, 1, 5, tzinfo=timezone.utc),
            "is_aggregator": False,
            "source": "alpha",
            "source_url": "https://example.test/5",
            "fetched_at": now,
        },
    ]

    with TestingSession() as session:
        session.execute(ThreatIntelRef.__table__.delete())
        session.add_all([ThreatIntelRef(**row) for row in rows])
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threat-intel/aggregator-breakdown")
    def endpoint(db: Session = Depends(get_session)):
        return get_aggregator_flag_breakdown(db)

    with TestClient(that_app) as client:
        response = client.get("/api/threat-intel/aggregator-breakdown")
        assert response.status_code == 200
        payload = response.json()
        assert payload["aggregator_rows"] == 2
        assert payload["direct_rows"] == 3
        assert payload["distinct_pulses"] == 4
        assert len(payload["by_indicator_type"]) == 2
        assert len(payload["by_source"]) == 2
        assert payload["oldest_pulse"] is not None
        assert payload["newest_pulse"] is not None

    print("PASS")


if __name__ == "__main__":
    _run_self_test()