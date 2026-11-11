from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Integer, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

try:
    from .logic import get_pulse_concentration as _get_pulse_concentration
except ImportError:
    _get_pulse_concentration = None


router = APIRouter()


class PulseConcentrationItem(BaseModel):
    pulse_name: str | None
    pulse_id: int | str | None
    indicator_count: int
    distinct_indicators: int
    indicator_types: list[str]
    is_aggregator: bool | None
    latest_pulse_created: datetime | str | None
    sources: list[str]


class PulseConcentrationResponse(BaseModel):
    total_pulses: int
    pulses: list[PulseConcentrationItem]


def _query_pulse_concentration(
    session: Session,
    limit: int,
) -> dict[str, Any]:
    is_aggregator = func.max(
        case(
            (ThreatIntelRef.is_aggregator.is_(None), None),
            else_=cast(ThreatIntelRef.is_aggregator, Integer),
        )
    )

    grouped = select(
        ThreatIntelRef.pulse_name.label("pulse_name"),
        ThreatIntelRef.pulse_id.label("pulse_id"),
        func.count(ThreatIntelRef.id).label("indicator_count"),
        func.count(func.distinct(ThreatIntelRef.indicator_value)).label(
            "distinct_indicators"
        ),
        is_aggregator.label("is_aggregator"),
        func.max(ThreatIntelRef.pulse_created).label("latest_pulse_created"),
    ).group_by(
        ThreatIntelRef.pulse_name,
        ThreatIntelRef.pulse_id,
    )

    rows = session.execute(grouped).all()
    rows = sorted(
        rows,
        key=lambda row: (
            -(row.indicator_count or 0),
            row.pulse_name is None,
            row.pulse_name or "",
            row.pulse_id is None,
            str(row.pulse_id or ""),
        ),
    )

    selected = rows[:limit]
    conditions = []
    for row in selected:
        pulse_name_condition = (
            ThreatIntelRef.pulse_name.is_(None)
            if row.pulse_name is None
            else ThreatIntelRef.pulse_name == row.pulse_name
        )
        pulse_id_condition = (
            ThreatIntelRef.pulse_id.is_(None)
            if row.pulse_id is None
            else ThreatIntelRef.pulse_id == row.pulse_id
        )
        conditions.append(
            and_(pulse_name_condition, pulse_id_condition)
        )

    details: dict[tuple[Any, Any], dict[str, set[str]]] = {}
    if conditions:
        detail_rows = session.execute(
            select(
                ThreatIntelRef.pulse_name,
                ThreatIntelRef.pulse_id,
                ThreatIntelRef.indicator_type,
                ThreatIntelRef.source,
            ).where(or_(*conditions))
        ).all()

        for detail in detail_rows:
            key = (detail.pulse_name, detail.pulse_id)
            values = details.setdefault(
                key,
                {"indicator_types": set(), "sources": set()},
            )
            if detail.indicator_type is not None:
                values["indicator_types"].add(str(detail.indicator_type))
            if detail.source is not None:
                values["sources"].add(str(detail.source))

    pulses = []
    for row in selected:
        detail = details.get(
            (row.pulse_name, row.pulse_id),
            {"indicator_types": set(), "sources": set()},
        )
        pulses.append(
            {
                "pulse_name": row.pulse_name,
                "pulse_id": row.pulse_id,
                "indicator_count": int(row.indicator_count or 0),
                "distinct_indicators": int(row.distinct_indicators or 0),
                "indicator_types": sorted(detail["indicator_types"]),
                "is_aggregator": (
                    bool(row.is_aggregator)
                    if row.is_aggregator is not None
                    else None
                ),
                "latest_pulse_created": row.latest_pulse_created,
                "sources": sorted(detail["sources"]),
            }
        )

    return {
        "total_pulses": len(rows),
        "pulses": pulses,
    }


@router.get(
    "/threat-intel/pulses/top",
    response_model=PulseConcentrationResponse,
)
def get_top_pulses(
    limit: int = Query(default=20),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    clamped_limit = max(1, min(100, limit))
    if _get_pulse_concentration is not None:
        return _get_pulse_concentration(session, clamped_limit)
    return _query_pulse_concentration(session, clamped_limit)


if __name__ == "__main__":
    from datetime import timezone

    from fastapi import FastAPI
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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_session = TestSession()

    now = datetime.now(timezone.utc)
    test_session.add_all(
        [
            ThreatIntelRef(
                id=1,
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id=10,
                pulse_name="Alpha Pulse",
                pulse_created=now,
                is_aggregator=False,
                source="feed-a",
                source_url="https://example.test/a/1",
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id=10,
                pulse_name="Alpha Pulse",
                pulse_created=now,
                is_aggregator=False,
                source="feed-b",
                source_url="https://example.test/a/2",
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="ip",
                indicator_value="198.51.100.5",
                pulse_id=20,
                pulse_name="Beta Pulse",
                pulse_created=now,
                is_aggregator=True,
                source="feed-a",
                source_url="https://example.test/b/1",
                fetched_at=now,
            ),
        ]
    )
    test_session.commit()

    def override_get_session() -> Session:
        return test_session

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/threat-intel/pulses/top")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["pulses"]) == 2
        assert payload["pulses"][0]["indicator_count"] == 2
        assert payload["pulses"][0]["distinct_indicators"] == 2

        limited = client.get("/api/threat-intel/pulses/top?limit=1")
        assert limited.status_code == 200
        assert len(limited.json()["pulses"]) == 1

    test_session.close()
    test_engine.dispose()
    print("PASS")