"""Logic for the threat intelligence indicator pivot API."""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef


class ThreatIntelIndicator(BaseModel):
    id: int
    indicator_type: str
    indicator_value: str
    pulse_id: Optional[str] = None
    pulse_name: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    is_aggregator: bool
    fetched_at: Optional[str] = None


class ThreatIntelIndicatorPivotResponse(BaseModel):
    total: int
    returned: int
    distinct_pulses: int
    counts_by_type: Dict[str, int]
    counts_by_source: Dict[str, int]
    aggregator_count: int
    newest_fetched_at: Optional[str] = None
    indicators: list[ThreatIntelIndicator]


def _as_text(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def list_threat_intel_indicators(
    db: Session,
    indicator_type: Optional[str] = None,
    limit: int = 100,
) -> ThreatIntelIndicatorPivotResponse:
    """Return a filtered page and aggregate statistics for all indicators."""
    limit = max(1, min(int(limit), 500))

    total = int(db.execute(select(func.count()).select_from(ThreatIntelRef)).scalar() or 0)

    type_rows = db.execute(
        select(ThreatIntelRef.indicator_type, func.count())
        .group_by(ThreatIntelRef.indicator_type)
    ).all()
    counts_by_type: Dict[str, int] = {}
    for row_type, count in type_rows:
        counts_by_type[str(row_type) if row_type is not None else "unknown"] = int(count or 0)

    source_rows = db.execute(
        select(ThreatIntelRef.source, func.count())
        .group_by(ThreatIntelRef.source)
    ).all()
    counts_by_source: Dict[str, int] = {}
    for row_source, count in source_rows:
        counts_by_source[str(row_source) if row_source is not None else "unknown"] = int(count or 0)

    aggregator_count = int(
        db.execute(
            select(func.count())
            .select_from(ThreatIntelRef)
            .where(ThreatIntelRef.is_aggregator.is_(True))
        ).scalar()
        or 0
    )

    distinct_pulses = int(
        db.execute(
            select(func.count(distinct(ThreatIntelRef.pulse_id)))
            .select_from(ThreatIntelRef)
        ).scalar()
        or 0
    )

    newest_value = db.execute(
        select(func.max(ThreatIntelRef.fetched_at))
    ).scalar()

    query = select(ThreatIntelRef)
    if indicator_type:
        query = query.where(ThreatIntelRef.indicator_type == indicator_type)
    query = query.order_by(ThreatIntelRef.fetched_at.desc()).limit(limit)
    rows = db.execute(query).scalars().all()

    indicators = [
        ThreatIntelIndicator(
            id=int(row.id),
            indicator_type=str(row.indicator_type),
            indicator_value=str(row.indicator_value),
            pulse_id=_as_text(row.pulse_id),
            pulse_name=_as_text(row.pulse_name),
            source=_as_text(row.source),
            source_url=_as_text(row.source_url),
            is_aggregator=bool(row.is_aggregator),
            fetched_at=_as_text(row.fetched_at),
        )
        for row in rows
    ]

    return ThreatIntelIndicatorPivotResponse(
        total=total,
        returned=len(indicators),
        distinct_pulses=distinct_pulses,
        counts_by_type=counts_by_type,
        counts_by_source=counts_by_source,
        aggregator_count=aggregator_count,
        newest_fetched_at=_as_text(newest_value),
        indicators=indicators,
    )


router = APIRouter(prefix="/api")


@router.get(
    "/threat-intel/indicators",
    response_model=ThreatIntelIndicatorPivotResponse,
)
def get_threat_intel_indicators(
    indicator_type: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> ThreatIntelIndicatorPivotResponse:
    return list_threat_intel_indicators(db, indicator_type=indicator_type, limit=limit)


app = FastAPI()
app.include_router(router)


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

    def test_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = test_session

    db = TestSession()
    db.add_all(
        [
            ThreatIntelRef(
                id=1,
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-1",
                pulse_name="Example Pulse",
                is_aggregator=True,
                source="example",
                source_url="https://example.test/1",
                fetched_at=datetime(2024, 1, 2, 3, 4, 5),
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="ip",
                indicator_value="192.0.2.2",
                pulse_id="pulse-2",
                pulse_name="Second Pulse",
                is_aggregator=False,
                source="example",
                source_url="https://example.test/2",
                fetched_at=datetime(2024, 1, 1, 3, 4, 5),
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id="pulse-1",
                pulse_name="Example Pulse",
                is_aggregator=False,
                source="other",
                source_url="https://other.test/3",
                fetched_at=datetime(2024, 1, 3, 3, 4, 5),
            ),
        ]
    )
    db.commit()
    db.close()

    client = TestClient(that_app)
    response = client.get("/api/threat-intel/indicators")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["returned"] == 3
    assert payload["counts_by_type"]["ip"] == 2
    assert payload["counts_by_type"]["domain"] == 1
    assert payload["aggregator_count"] == 1

    filtered = client.get("/api/threat-intel/indicators?indicator_type=ip")
    assert filtered.status_code == 200
    assert filtered.json()["returned"] == 2

    print("PASS")