from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.models import McpServerRegistry, PerspectiveEvent


class PerspectiveEventResponse(BaseModel):
    id: int
    server_id: str
    server_name: Optional[str]
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: datetime


class PerspectiveEventsResponse(BaseModel):
    perspective_id: str
    since: Optional[str]
    count: int
    events: list[PerspectiveEventResponse]


app = FastAPI()


@app.get(
    "/api/perspectives/{perspective_id}/events",
    response_model=PerspectiveEventsResponse,
)
def list_perspective_events(
    perspective_id: str,
    since: Optional[datetime] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> PerspectiveEventsResponse:
    limit = min(limit, 500)

    statement = (
        select(
            PerspectiveEvent.id,
            PerspectiveEvent.server_id,
            McpServerRegistry.name.label("server_name"),
            PerspectiveEvent.change_type,
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
            PerspectiveEvent.seen,
            PerspectiveEvent.created_at,
        )
        .select_from(PerspectiveEvent)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == PerspectiveEvent.server_id,
        )
        .where(PerspectiveEvent.perspective_id == perspective_id)
    )

    if since is not None:
        statement = statement.where(PerspectiveEvent.created_at > since)

    statement = statement.order_by(PerspectiveEvent.created_at.desc()).limit(limit)

    rows = db.execute(statement).mappings().all()

    events = [
        PerspectiveEventResponse(
            id=row["id"],
            server_id=row["server_id"],
            server_name=row["server_name"],
            change_type=row["change_type"],
            old_tier=row["old_tier"],
            new_tier=row["new_tier"],
            seen=bool(row["seen"]),
            created_at=row["created_at"],
        )
        for row in rows
    ]

    return PerspectiveEventsResponse(
        perspective_id=perspective_id,
        since=since.isoformat() if since is not None else None,
        count=len(events),
        events=events,
    )


def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    local_app = FastAPI()
    local_app.include_router(app.router)
    local_app.dependency_overrides[get_session] = override_get_session

    now = datetime(2024, 1, 1, 12, 0, 0)
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="server-1", name="Registry Server One"),
                PerspectiveEvent(
                    perspective_id="perspective-1",
                    server_id="server-1",
                    change_type="tier_changed",
                    old_tier="low",
                    new_tier="high",
                    seen=True,
                    created_at=now,
                ),
                PerspectiveEvent(
                    perspective_id="perspective-1",
                    server_id="server-1",
                    change_type="seen",
                    old_tier=None,
                    new_tier="high",
                    seen=False,
                    created_at=now.replace(minute=1),
                ),
                PerspectiveEvent(
                    perspective_id="perspective-2",
                    server_id="server-1",
                    change_type="tier_changed",
                    old_tier="low",
                    new_tier="medium",
                    seen=True,
                    created_at=now.replace(minute=2),
                ),
            ]
        )
        session.commit()

    with TestClient(local_app) as client:
        response = client.get("/api/perspectives/perspective-1/events")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["count"] == 2, payload
        assert len(payload["events"]) == 2, payload
        assert payload["events"][0]["server_name"] == "Registry Server One", payload
        assert isinstance(payload["events"][0]["seen"], bool), payload

    print("PASS")


if __name__ == "__main__":
    _run_self_test()