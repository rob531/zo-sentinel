"""HTTP router for perspective unseen events."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective

from .logic import get_unseen_events

router = APIRouter(prefix="/api", tags=["perspective_unseen_events"])


class UnseenEvent(BaseModel):
    server_id: str
    change_type: str
    old_tier: str | None
    new_tier: str | None
    created_at: datetime


class UnseenEventsResponse(BaseModel):
    perspective_id: int
    perspective_name: str
    unseen: list[UnseenEvent]
    count: int


@router.get(
    "/perspectives/{perspective_id}/unseen-events",
    response_model=UnseenEventsResponse,
)
def perspective_unseen_events(
    perspective_id: int,
    db: Session = Depends(get_session),
) -> UnseenEventsResponse:
    if db.get(Perspective, perspective_id) is None:
        raise HTTPException(status_code=404, detail="Perspective not found")

    payload: dict[str, Any] = get_unseen_events(db, perspective_id)
    return UnseenEventsResponse(**payload)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import PerspectiveEvent

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Perspective.metadata.create_all(
        test_engine,
        tables=[Perspective.__table__, PerspectiveEvent.__table__],
    )
    TestSession = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    with TestSession() as session:
        session.add(
            Perspective(
                id=1,
                name="Contract Perspective",
                description="Self-test perspective",
                facet_filters={},
                created_by="self-test",
            )
        )
        session.add_all(
            [
                PerspectiveEvent(
                    id=1,
                    perspective_id=1,
                    server_id="server-unseen-1",
                    change_type="tier_changed",
                    old_tier="medium",
                    new_tier="high",
                    seen=False,
                ),
                PerspectiveEvent(
                    id=2,
                    perspective_id=1,
                    server_id="server-unseen-2",
                    change_type="tier_changed",
                    old_tier="low",
                    new_tier="critical",
                    seen=False,
                ),
                PerspectiveEvent(
                    id=3,
                    perspective_id=1,
                    server_id="server-seen",
                    change_type="tier_changed",
                    old_tier="high",
                    new_tier="medium",
                    seen=True,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/perspectives/1/unseen-events")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["count"] == 2
        assert body["perspective_id"] == 1
        assert body["perspective_name"] == "Contract Perspective"
        assert {event["server_id"] for event in body["unseen"]} == {
            "server-unseen-1",
            "server-unseen-2",
        }

        missing = client.get("/api/perspectives/999/unseen-events")
        assert missing.status_code == 404, missing.text

    print("PASS")