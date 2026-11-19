from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_perspective_activity

router = APIRouter(prefix="/api", tags=["perspectives"])


class PerspectiveActivityEvent(BaseModel):
    id: int
    perspective_id: str
    perspective_name: str | None
    server_id: str
    change_type: str
    old_tier: str | None
    new_tier: str | None
    seen: bool
    created_at: str


class TransitionCount(BaseModel):
    old_tier: str | None
    new_tier: str | None
    count: int


class PerspectiveActivityResponse(BaseModel):
    limit: int
    generated_at: str
    events: list[PerspectiveActivityEvent]
    transitions: list[TransitionCount]


def _event_model(value: Any) -> PerspectiveActivityEvent:
    if isinstance(value, dict):
        return PerspectiveActivityEvent.model_validate(value)
    if hasattr(value, "model_dump"):
        return PerspectiveActivityEvent.model_validate(value.model_dump())
    if hasattr(value, "dict"):
        return PerspectiveActivityEvent.model_validate(value.dict())
    return PerspectiveActivityEvent.model_validate(
        {
            field: getattr(value, field)
            for field in PerspectiveActivityEvent.model_fields
        }
    )


def _transition_model(value: Any) -> TransitionCount:
    if isinstance(value, dict):
        return TransitionCount.model_validate(value)
    if hasattr(value, "model_dump"):
        return TransitionCount.model_validate(value.model_dump())
    if hasattr(value, "dict"):
        return TransitionCount.model_validate(value.dict())
    return TransitionCount.model_validate(
        {
            field: getattr(value, field)
            for field in TransitionCount.model_fields
        }
    )


@router.get(
    "/perspectives/activity",
    response_model=PerspectiveActivityResponse,
)
def get_activity(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> PerspectiveActivityResponse:
    result = get_perspective_activity(db, limit=limit)

    if isinstance(result, dict):
        raw_events = result.get("events", [])
        raw_transitions = result.get("transitions", [])
    else:
        raw_events, raw_transitions = result

    return PerspectiveActivityResponse(
        limit=limit,
        generated_at=datetime.now(timezone.utc).isoformat(),
        events=[_event_model(event) for event in raw_events],
        transitions=[_transition_model(transition) for transition in raw_transitions],
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Org, Perspective, PerspectiveEvent

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(test_engine)
    Perspective.__table__.create(test_engine)
    PerspectiveEvent.__table__.create(test_engine)

    TestingSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    test_db = TestingSession()

    test_db.add_all(
        [
            Perspective(
                id="perspective-1",
                name="Primary",
                created_by="contract",
                description="",
                facet_filters="{}",
                org_id=None,
            ),
            Perspective(
                id="perspective-2",
                name="Secondary",
                created_by="contract",
                description="",
                facet_filters="{}",
                org_id=None,
            ),
            PerspectiveEvent(
                id=1,
                perspective_id="perspective-1",
                server_id="server-1",
                change_type="tier_change",
                old_tier="CAUTION_LIMITED",
                new_tier="HIGH_RISK_ISOLATED",
                seen=False,
            ),
            PerspectiveEvent(
                id=2,
                perspective_id="missing-perspective",
                server_id="server-2",
                change_type="tier_change",
                old_tier="CAUTION_LIMITED",
                new_tier="HIGH_RISK_ISOLATED",
                seen=False,
            ),
            PerspectiveEvent(
                id=3,
                perspective_id="perspective-2",
                server_id="server-3",
                change_type="tier_change",
                old_tier="HIGH_RISK_ISOLATED",
                new_tier="TRUSTED_GENERAL",
                seen=True,
            ),
        ]
    )
    test_db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_db

    response = TestClient(test_app).get("/api/perspectives/activity?limit=3")
    assert response.status_code == 200
    payload = response.json()
    assert payload["limit"] == 3
    assert len(payload["events"]) == 3
    assert any(event["perspective_name"] is None for event in payload["events"])

    transitions = {
        (transition["old_tier"], transition["new_tier"]): transition["count"]
        for transition in payload["transitions"]
    }
    assert transitions[("CAUTION_LIMITED", "HIGH_RISK_ISOLATED")] == 2
    assert transitions[("HIGH_RISK_ISOLATED", "TRUSTED_GENERAL")] == 1

    print("PASS")
    test_db.close()
    test_engine.dispose()