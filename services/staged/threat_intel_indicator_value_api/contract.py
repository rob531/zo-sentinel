from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef


router = APIRouter(prefix="/api")


def _serialise(row: ThreatIntelRef) -> dict[str, Any]:
    return {
        "indicator_value": row.indicator_value,
        "pulse_id": row.pulse_id,
        "pulse_name": row.pulse_name,
        "is_aggregator": row.is_aggregator,
        "source": row.source,
        "source_url": row.source_url,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
    }


def _aggregate(rows: list[ThreatIntelRef], indicator_type: str | None) -> dict[str, Any]:
    return {
        "indicator_type": indicator_type,
        "total_rows": len(rows),
        "distinct_indicator_values": len({row.indicator_value for row in rows}),
        "distinct_pulses": len({row.pulse_id for row in rows}),
        "aggregator_rows": sum(1 for row in rows if row.is_aggregator),
        "newest_fetched_at": (
            max(row.fetched_at for row in rows if row.fetched_at).isoformat()
            if any(row.fetched_at for row in rows)
            else None
        ),
    }


def get_indicator_values(
    session: Session,
    indicator_type: str | None = None,
    limit: int = 25,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    limit = max(1, min(100, limit))
    statement = select(ThreatIntelRef)
    if indicator_type is not None:
        statement = statement.where(ThreatIntelRef.indicator_type == indicator_type)
    statement = statement.order_by(
        ThreatIntelRef.fetched_at.desc().nullslast(),
        ThreatIntelRef.id.desc(),
    )

    rows = list(session.scalars(statement).all())
    all_rows = (
        list(session.scalars(select(ThreatIntelRef)).all())
        if indicator_type is not None
        else rows
    )
    aggregate = _aggregate(all_rows, indicator_type)

    if indicator_type is None:
        grouped: dict[str, list[ThreatIntelRef]] = {}
        for row in rows:
            grouped.setdefault(row.indicator_type, []).append(row)
        items = [
            {
                "indicator_type": row_type,
                **_aggregate(type_rows, row_type),
            }
            for row_type, type_rows in sorted(grouped.items())
        ]
        return items, aggregate

    return [_serialise(row) for row in rows[:limit]], aggregate


@router.get("/threat-intel/indicator-value")
def indicator_value_endpoint(
    indicator_type: str | None = Query(default=None),
    limit: int = Query(default=25),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    items, aggregate = get_indicator_values(session, indicator_type, limit)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "indicator_type": indicator_type,
        "items": items,
        "aggregate": aggregate,
    }


app = FastAPI()
app.include_router(router)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine, checkfirst=True)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    seed_rows = [
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-a",
            pulse_name="Pulse A",
            pulse_created=now,
            is_aggregator=False,
            source="test",
            source_url="https://example.test/a",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="pulse-a",
            pulse_name="Pulse A",
            pulse_created=now,
            is_aggregator=False,
            source="test",
            source_url="https://example.test/a2",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="pulse-a",
            pulse_name="Pulse A",
            pulse_created=now,
            is_aggregator=True,
            source="test",
            source_url="https://example.test/d",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="domain",
            indicator_value="example.test",
            pulse_id="pulse-b",
            pulse_name="Pulse B",
            pulse_created=now,
            is_aggregator=True,
            source="test",
            source_url="https://example.test/d2",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=5,
            indicator_type="domain",
            indicator_value="example.org",
            pulse_id="pulse-b",
            pulse_name="Pulse B",
            pulse_created=now,
            is_aggregator=False,
            source="test",
            source_url="https://example.test/d3",
            fetched_at=now,
        ),
    ]

    with testing_session() as session:
        session.add_all(seed_rows)
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/threat-intel/indicator-value",
            params={"indicator_type": "domain", "limit": 2},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["aggregate"]["total_rows"] == 5
        assert payload["aggregate"]["distinct_pulses"] == 2
        assert len(payload["items"]) <= 2

        grouped_response = client.get("/api/threat-intel/indicator-value")
        assert grouped_response.status_code == 200

    print("PASS")


if __name__ == "__main__":
    _run_self_test()