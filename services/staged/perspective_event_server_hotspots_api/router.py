from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import PerspectiveEvent

from .logic import get_server_hotspots


class ServerHotspot(BaseModel):
    server_id: str
    event_count: int
    perspective_count: int
    change_types: int
    latest_change_at: Optional[datetime] = None


class ServerHotspotsResponse(BaseModel):
    days: Optional[int] = None
    limit: int
    hotspots: list[ServerHotspot] = Field(default_factory=list)


router = APIRouter(prefix="/api", tags=["perspective-event-server-hotspots-api"])


@router.get(
    "/perspectives/events/server-hotspots",
    response_model=ServerHotspotsResponse,
)
def server_hotspots(
    days: Optional[int] = Query(default=None, ge=1, le=3650),
    limit: int = Query(default=25, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> ServerHotspotsResponse:
    rows = get_server_hotspots(session=session, days=days, limit=limit)
    return ServerHotspotsResponse(
        days=days,
        limit=limit,
        hotspots=[ServerHotspot(**row) for row in rows],
    )


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PerspectiveEvent.__table__.create(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seed_rows = [
        (1, "perspective-a", "server-a", "tier_up", "low", "medium", 0, now - timedelta(days=1)),
        (2, "perspective-b", "server-a", "tier_down", "medium", "low", 1, now - timedelta(days=2)),
        (3, "perspective-a", "server-a", "trust_change", "low", "high", 0, now - timedelta(days=3)),
        (4, "perspective-a", "server-b", "tier_up", "low", "medium", 0, now - timedelta(days=2)),
        (5, "perspective-b", "server-b", "tier_up", "low", "medium", 1, now - timedelta(days=4)),
        (6, "perspective-a", "server-c", "tier_down", "medium", "low", 0, now - timedelta(days=5)),
    ]

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO perspective_events (
                    id, perspective_id, server_id, change_type,
                    old_tier, new_tier, seen, created_at
                ) VALUES (
                    :id, :perspective_id, :server_id, :change_type,
                    :old_tier, :new_tier, :seen, :created_at
                )
                """
            ),
            [
                {
                    "id": row[0],
                    "perspective_id": row[1],
                    "server_id": row[2],
                    "change_type": row[3],
                    "old_tier": row[4],
                    "new_tier": row[5],
                    "seen": row[6],
                    "created_at": row[7],
                }
                for row in seed_rows
            ],
        )

    def override_get_session():
        with Session(engine) as session:
            yield session

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get(
                "/api/perspectives/events/server-hotspots",
                params={"days": 30, "limit": 25},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["days"] == 30
            assert payload["limit"] == 25
            assert payload["hotspots"][0]["server_id"] == "server-a"
            assert payload["hotspots"][0]["event_count"] == 3
            assert payload["hotspots"][0]["perspective_count"] == 2
            assert payload["hotspots"][0]["change_types"] == 3
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()