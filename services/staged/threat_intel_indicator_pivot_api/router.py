from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, desc, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ThreatIntelRef

try:
    from .logic import pivot_indicators as _logic_pivot_indicators
except ImportError:
    _logic_pivot_indicators = None


router = APIRouter(prefix="/api", tags=["threat-intel"])


class IndicatorItem(BaseModel):
    id: int
    indicator_type: str
    indicator_value: str
    pulse_id: str | None
    pulse_name: str | None
    source: str | None
    source_url: str | None
    is_aggregator: bool
    fetched_at: str | None


class IndicatorPivotResponse(BaseModel):
    total: int
    returned: int
    distinct_pulses: int
    counts_by_type: dict[str, int]
    counts_by_source: dict[str, int]
    aggregator_count: int
    newest_fetched_at: str | None
    indicators: list[IndicatorItem]


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _pivot_indicators(
    db: Session,
    indicator_type: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    total = int(
        db.execute(select(func.count(ThreatIntelRef.id))).scalar_one()
    )

    type_rows = db.execute(
        select(ThreatIntelRef.indicator_type, func.count(ThreatIntelRef.id))
        .group_by(ThreatIntelRef.indicator_type)
    ).all()
    source_rows = db.execute(
        select(ThreatIntelRef.source, func.count(ThreatIntelRef.id))
        .group_by(ThreatIntelRef.source)
    ).all()

    aggregator_count = int(
        db.execute(
            select(func.count(ThreatIntelRef.id)).where(
                ThreatIntelRef.is_aggregator.is_(True)
            )
        ).scalar_one()
    )

    distinct_pulses = int(
        db.execute(
            select(func.count(func.distinct(ThreatIntelRef.pulse_id)))
        ).scalar_one()
    )

    newest_fetched_at = db.execute(
        select(func.max(ThreatIntelRef.fetched_at))
    ).scalar_one()

    query = select(ThreatIntelRef)
    if indicator_type is not None:
        query = query.where(ThreatIntelRef.indicator_type == indicator_type)
    query = query.order_by(desc(ThreatIntelRef.fetched_at)).limit(limit)

    rows = db.execute(query).scalars().all()

    return {
        "total": total,
        "returned": len(rows),
        "distinct_pulses": distinct_pulses,
        "counts_by_type": {
            str(row.indicator_type): int(row.count)
            for row in type_rows
        },
        "counts_by_source": {
            str(row.source): int(row.count)
            for row in source_rows
        },
        "aggregator_count": aggregator_count,
        "newest_fetched_at": _timestamp(newest_fetched_at),
        "indicators": [
            IndicatorItem(
                id=int(row.id),
                indicator_type=str(row.indicator_type),
                indicator_value=str(row.indicator_value),
                pulse_id=row.pulse_id,
                pulse_name=row.pulse_name,
                source=row.source,
                source_url=row.source_url,
                is_aggregator=bool(row.is_aggregator),
                fetched_at=_timestamp(row.fetched_at),
            )
            for row in rows
        ],
    }


@router.get(
    "/threat-intel/indicators",
    response_model=IndicatorPivotResponse,
)
def get_indicators(
    indicator_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> IndicatorPivotResponse:
    result = _pivot_indicators(
        db,
        indicator_type=indicator_type,
        limit=limit,
    )
    return IndicatorPivotResponse(**result)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ThreatIntelRef.__table__.create(engine)

    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()

    now = datetime(2025, 1, 1, 12, 0, 0)
    db.add_all(
        [
            ThreatIntelRef(
                id=1,
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-1",
                pulse_name="Pulse One",
                source="alpha",
                source_url="https://example.test/1",
                is_aggregator=False,
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="ip",
                indicator_value="192.0.2.2",
                pulse_id="pulse-2",
                pulse_name="Pulse Two",
                source="beta",
                source_url="https://example.test/2",
                is_aggregator=True,
                fetched_at=datetime(2025, 1, 2, 12, 0, 0),
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id="pulse-3",
                pulse_name="Pulse Three",
                source="alpha",
                source_url="https://example.test/3",
                is_aggregator=False,
                fetched_at=datetime(2025, 1, 3, 12, 0, 0),
            ),
        ]
    )
    db.commit()

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        yield db

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        response = client.get("/api/threat-intel/indicators")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert payload["returned"] == 3
        assert payload["counts_by_type"]["ip"] == 2
        assert payload["counts_by_type"]["domain"] == 1
        assert payload["aggregator_count"] == 1

        response = client.get(
            "/api/threat-intel/indicators",
            params={"indicator_type": "ip"},
        )
        assert response.status_code == 200
        assert response.json()["returned"] == 2

    db.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()