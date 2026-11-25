"""HTTP surface for the perspective tier-event stream service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveEvent

from .logic import get_tier_event_stream

router = APIRouter(prefix="/api", tags=["perspectives"])


class DailyTierEvents(BaseModel):
    date: str
    events: int
    escalations: int
    deescalations: int
    unique_servers: int
    distinct_servers_changed: int


class PerspectiveRollup(BaseModel):
    perspective_id: str | int | UUID | None
    perspective_name: str | None
    events: int
    unique_servers: int
    most_common_transition: str | None


class TierTransition(BaseModel):
    transition: str
    count: int


class TierEventSummary(BaseModel):
    total_events: int
    escalations: int
    deescalations: int
    unique_servers: int
    top_transitions: list[TierTransition]


class TierEventItem(BaseModel):
    event_id: str | int | UUID
    perspective_id: str | int | UUID | None
    perspective_name: str | None
    server_id: str | int | UUID
    server_name: str | None
    change_type: str
    old_tier: str | None
    new_tier: str | None
    seen: bool
    created_at: datetime


class TierEventStreamResponse(BaseModel):
    days: int
    series: list[DailyTierEvents]
    by_perspective: list[PerspectiveRollup]
    summary: TierEventSummary
    items: list[TierEventItem]


@router.get(
    "/perspectives/tier-events",
    response_model=TierEventStreamResponse,
    status_code=200,
)
def get_tier_events(
    days: int = Query(default=30, ge=1, le=90),
    perspective_id: str | None = Query(default=None),
    db: Session = Depends(get_session),
) -> TierEventStreamResponse:
    return TierEventStreamResponse(
        **get_tier_event_stream(db, days=days, perspective_id=perspective_id)
    )


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    for model in (McpServerRegistry, Perspective, PerspectiveEvent):
        model.__table__.create(engine)

    def identifier(column: Any, prefix: str) -> Any:
        try:
            python_type = column.type.python_type
        except NotImplementedError:
            python_type = str
        if python_type is int:
            return abs(hash(prefix)) % 1_000_000
        if python_type is UUID:
            return UUID(prefix)
        return prefix

    perspective_columns = Perspective.__table__.columns
    event_columns = PerspectiveEvent.__table__.columns
    server_columns = McpServerRegistry.__table__.columns

    perspective_one = identifier(perspective_columns["id"], "11111111-1111-4111-8111-111111111111")
    perspective_two = identifier(perspective_columns["id"], "22222222-2222-4222-8222-222222222222")
    server_one = identifier(server_columns["server_id"], "33333333-3333-4333-8333-333333333333")
    server_two = identifier(server_columns["server_id"], "44444444-4444-4444-8444-444444444444")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    yesterday = now - timedelta(days=1)

    with TestingSession() as db:
        db.add_all(
            [
                Perspective(
                    id=perspective_one,
                    name="Production",
                    description="Production perspective",
                    created_by="self-test",
                    facet_filters={},
                ),
                Perspective(
                    id=perspective_two,
                    name="Development",
                    description="Development perspective",
                    created_by="self-test",
                    facet_filters={},
                ),
                McpServerRegistry(server_id=server_one, name="Server One"),
                McpServerRegistry(server_id=server_two, name="Server Two"),
            ]
        )
        db.flush()

        event_values = [
            (
                identifier(event_columns["id"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"),
                perspective_one,
                server_one,
                "low",
                "medium",
                now,
            ),
            (
                identifier(event_columns["id"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"),
                perspective_one,
                server_two,
                "medium",
                "high",
                now,
            ),
            (
                identifier(event_columns["id"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3"),
                perspective_two,
                server_one,
                "high",
                "medium",
                yesterday,
            ),
            (
                identifier(event_columns["id"], "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4"),
                perspective_two,
                server_two,
                "medium",
                "low",
                yesterday,
            ),
        ]
        for event_id, perspective_id, server_id, old_tier, new_tier, created_at in event_values:
            db.add(
                PerspectiveEvent(
                    id=event_id,
                    perspective_id=perspective_id,
                    server_id=server_id,
                    change_type="tier_change",
                    old_tier=old_tier,
                    new_tier=new_tier,
                    seen=True,
                    created_at=created_at,
                )
            )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestingSession() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/perspectives/tier-events",
        params={"days": 2},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["days"] == 2
    assert len(payload["series"]) == 2
    assert sum(point["events"] for point in payload["series"]) == 4
    assert any(point["events"] == 2 for point in payload["series"])
    assert payload["summary"]["total_events"] == 4
    assert len(payload["items"]) == 4


if __name__ == "__main__":
    _self_test()
    print("PASS")