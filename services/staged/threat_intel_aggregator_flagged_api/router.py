from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

try:
    from .logic import ThreatIntelRef as LogicThreatIntelRef
except ImportError:
    try:
        from logic import ThreatIntelRef as LogicThreatIntelRef
    except ImportError:
        LogicThreatIntelRef = ThreatIntelRef

router = APIRouter(prefix="/api")


class ThreatIntelAggregatorGroup(BaseModel):
    indicator_type: str
    total: int
    aggregator_count: int
    distinct_pulses: int
    oldest_fetched_at: datetime | None
    max_age_hours: float | None


class ThreatIntelAggregatorResponse(BaseModel):
    groups: list[ThreatIntelAggregatorGroup]
    total_refs: int


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _inventory(session: Session) -> ThreatIntelAggregatorResponse:
    model = LogicThreatIntelRef if LogicThreatIntelRef is ThreatIntelRef else ThreatIntelRef
    now = datetime.now(UTC)

    statement = (
        select(
            model.indicator_type.label("indicator_type"),
            func.count(model.id).label("total"),
            func.coalesce(
                func.sum(
                    case(
                        (model.is_aggregator.is_(True), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("aggregator_count"),
            func.count(distinct(model.pulse_id)).label("distinct_pulses"),
            func.min(model.fetched_at).label("oldest_fetched_at"),
        )
        .group_by(model.indicator_type)
    )

    groups: list[ThreatIntelAggregatorGroup] = []
    total_refs = 0

    for row in session.execute(statement):
        oldest = row.oldest_fetched_at
        max_age_hours = None
        if oldest is not None:
            max_age_hours = max(
                0.0,
                (now - _as_utc(oldest)).total_seconds() / 3600.0,
            )

        total = int(row.total or 0)
        total_refs += total
        groups.append(
            ThreatIntelAggregatorGroup(
                indicator_type=str(row.indicator_type),
                total=total,
                aggregator_count=int(row.aggregator_count or 0),
                distinct_pulses=int(row.distinct_pulses or 0),
                oldest_fetched_at=oldest,
                max_age_hours=max_age_hours,
            )
        )

    groups.sort(key=lambda group: group.indicator_type)
    return ThreatIntelAggregatorResponse(groups=groups, total_refs=total_refs)


@router.get(
    "/threat-intel/aggregators",
    response_model=ThreatIntelAggregatorResponse,
)
async def get_threat_intel_aggregators(
    session: Session = Depends(get_session),
) -> ThreatIntelAggregatorResponse:
    return _inventory(session)


def _self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    ThreatIntelRef.__table__.create(engine)

    now = datetime.now(UTC)
    rows = [
        ThreatIntelRef(
            id=1,
            indicator_type="ip",
            indicator_value="192.0.2.1",
            pulse_id="pulse-a",
            pulse_name="Pulse A",
            pulse_created=now,
            is_aggregator=True,
            source="self-test",
            source_url="https://example.invalid/1",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=2,
            indicator_type="ip",
            indicator_value="192.0.2.2",
            pulse_id="pulse-a",
            pulse_name="Pulse A",
            pulse_created=now,
            is_aggregator=True,
            source="self-test",
            source_url="https://example.invalid/2",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=3,
            indicator_type="ip",
            indicator_value="192.0.2.3",
            pulse_id="pulse-b",
            pulse_name="Pulse B",
            pulse_created=now,
            is_aggregator=False,
            source="self-test",
            source_url="https://example.invalid/3",
            fetched_at=now,
        ),
        ThreatIntelRef(
            id=4,
            indicator_type="domain",
            indicator_value="example.invalid",
            pulse_id="pulse-c",
            pulse_name="Pulse C",
            pulse_created=now,
            is_aggregator=False,
            source="self-test",
            source_url="https://example.invalid/4",
            fetched_at=now,
        ),
    ]

    with session_factory() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/threat-intel/aggregators")
            assert response.status_code == 200, response.text
            payload: dict[str, Any] = response.json()
            assert payload["total_refs"] == 4, payload

            groups = {group["indicator_type"]: group for group in payload["groups"]}
            assert groups["ip"]["total"] == 3, payload
            assert groups["ip"]["aggregator_count"] == 2, payload
            assert groups["domain"]["total"] == 1, payload
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()