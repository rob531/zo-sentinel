from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, PerspectiveEvent

router = APIRouter(prefix="/api", tags=["perspective_events"])


class EventDetail(BaseModel):
    id: int
    server_id: int
    server_name: str
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: str

    class Config:
        from_attributes = True


class EventsResponse(BaseModel):
    events: list[EventDetail]


@router.get("/perspective/{perspective_id}/events", response_model=EventsResponse)
def get_perspective_events(
    perspective_id: int,
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
):
    stmt = (
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
        .join(
            McpServerRegistry,
            PerspectiveEvent.server_id == McpServerRegistry.id,
        )
        .where(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(PerspectiveEvent.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = db.execute(stmt).fetchall()
    events = [
        EventDetail(
            id=row.id,
            server_id=row.server_id,
            server_name=row.server_name,
            change_type=row.change_type,
            old_tier=row.old_tier,
            new_tier=row.new_tier,
            seen=row.seen,
            created_at=row.created_at.isoformat() if row.created_at else "",
        )
        for row in rows
    ]
    return EventsResponse(events=events)


if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    client = TestClient(router)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    router.dependency_overrides[get_session] = override_get_session

    db = TestingSessionLocal()
    server = McpServerRegistry(
        id=1,
        name="test-server",
        repo_url="https://github.com/test/server",
    )
    db.add(server)
    db.commit()

    event1 = PerspectiveEvent(
        perspective_id=1,
        server_id=1,
        change_type="tier_change",
        old_tier="low",
        new_tier="medium",
        seen=False,
        created_at="2024-01-15 10:00:00",
    )
    event2 = PerspectiveEvent(
        perspective_id=1,
        server_id=1,
        change_type="tier_change",
        old_tier="medium",
        new_tier="high",
        seen=True,
        created_at="2024-01-15 11:00:00",
    )
    db.add(event1)
    db.add(event2)
    db.commit()
    db.close()

    response = client.get("/api/perspective/1/events")
    data = response.json()

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert "events" in data, "Response missing 'events' key"
    assert len(data["events"]) == 2, f"Expected 2 events, got {len(data['events'])}"

    for event in data["events"]:
        assert "id" in event
        assert "server_id" in event
        assert "server_name" in event
        assert "change_type" in event
        assert "old_tier" in event
        assert "new_tier" in event
        assert "seen" in event
        assert "created_at" in event

    print("PASS")
    sys.exit(0)