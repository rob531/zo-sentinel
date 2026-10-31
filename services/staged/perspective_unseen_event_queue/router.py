from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveEvent

from .logic import get_unseen_events


class UnseenEvent(BaseModel):
    id: int
    perspective_id: str
    server_id: str
    server_name: str | None
    change_type: str
    old_tier: str | None
    new_tier: str | None
    seen: bool
    created_at: str
    current_risk_tier: str | None


class UnseenEventQueueResponse(BaseModel):
    perspective_id: str | None
    limit: int
    total: int
    events: list[UnseenEvent]
    by_change_type: dict[str, int]


router = APIRouter(prefix="/api", tags=["perspectives"])


@router.get(
    "/perspectives/unseen-events",
    response_model=UnseenEventQueueResponse,
)
def list_unseen_events(
    limit: int = Query(default=50, ge=1),
    perspective_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> UnseenEventQueueResponse:
    events, total, by_change_type = get_unseen_events(
        session=session,
        limit=limit,
        perspective_id=perspective_id,
    )
    return UnseenEventQueueResponse(
        perspective_id=perspective_id,
        limit=limit,
        total=total,
        events=events,
        by_change_type=by_change_type,
    )


def _run_self_test() -> None:
    from datetime import datetime, timedelta

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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    Perspective.__table__.create(test_engine)
    McpServerRegistry.__table__.create(test_engine)
    PerspectiveEvent.__table__.create(test_engine)

    now = datetime(2026, 1, 2, 12, 0, 0)
    with TestSession() as session:
        session.add(
            Perspective(
                id="perspective-test-1",
                name="Test Perspective",
                created_by="test@example.com",
                description="Contract test perspective",
                facet_filters={},
            )
        )
        session.add(
            McpServerRegistry(
                server_id="server-test-1",
                name="Contract Test Server",
                risk_tier="high",
            )
        )
        session.add_all(
            [
                PerspectiveEvent(
                    id=1,
                    perspective_id="perspective-test-1",
                    server_id="server-test-1",
                    change_type="tier_raise",
                    old_tier="medium",
                    new_tier="high",
                    seen=False,
                    created_at=now - timedelta(minutes=2),
                ),
                PerspectiveEvent(
                    id=2,
                    perspective_id="perspective-test-1",
                    server_id="server-test-1",
                    change_type="tier_drop",
                    old_tier="high",
                    new_tier="low",
                    seen=False,
                    created_at=now - timedelta(minutes=1),
                ),
                PerspectiveEvent(
                    id=3,
                    perspective_id="perspective-test-1",
                    server_id="server-test-1",
                    change_type="tier_raise",
                    old_tier="low",
                    new_tier="medium",
                    seen=True,
                    created_at=now,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/perspectives/unseen-events",
            params={"limit": 10, "perspective_id": "perspective-test-1"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["perspective_id"] == "perspective-test-1"
        assert payload["limit"] == 10
        assert payload["total"] == 2
        assert len(payload["events"]) == 2
        assert all(event["seen"] is False for event in payload["events"])
        assert all(event["id"] != 3 for event in payload["events"])
        assert payload["by_change_type"] == {"tier_raise": 1, "tier_drop": 1}
        assert payload["events"][0]["server_name"] == "Contract Test Server"
        assert payload["events"][0]["current_risk_tier"] == "high"

    print("PASS")


if __name__ == "__main__":
    _run_self_test()