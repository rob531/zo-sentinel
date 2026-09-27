# deps: fastapi, sqlalchemy
"""Perspective Event History Service.

Returns paginated history of perspective membership/tier change events
from the app Postgres via the standard get_session dependency.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent

router = APIRouter(prefix="/api", tags=["perspective_event_history"])


class EventItem(BaseModel):
    perspective_id: str
    server_id: str
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: datetime


class EventsResponse(BaseModel):
    perspective_id: str
    events: List[EventItem]
    total: int
    limit: int
    offset: int


@router.get(
    "/perspectives/{perspective_id}/events",
    response_model=EventsResponse,
)
def get_perspective_events(
    perspective_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> EventsResponse:
    """Return paginated event history for a perspective, newest first."""
    total = (
        db.query(func.count())
        .select_from(PerspectiveEvent)
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .scalar()
    )

    rows = (
        db.query(PerspectiveEvent)
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(desc(PerspectiveEvent.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )

    events = [
        EventItem(
            perspective_id=r.perspective_id,
            server_id=r.server_id,
            change_type=r.change_type,
            old_tier=r.old_tier,
            new_tier=r.new_tier,
            seen=r.seen,
            created_at=r.created_at,
        )
        for r in rows
    ]

    return EventsResponse(
        perspective_id=perspective_id,
        events=events,
        total=total or 0,
        limit=limit,
        offset=offset,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base

    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(test_engine)

    app = FastAPI()
    app.include_router(router)

    def _override():
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override

    now = datetime.utcnow()
    with TestSessionLocal() as sess:
        sess.add(PerspectiveEvent(
            perspective_id="persp-abc",
            server_id="srv-001",
            change_type="entered",
            old_tier=None,
            new_tier="HIGH",
            seen=False,
            created_at=now,
        ))
        sess.add(PerspectiveEvent(
            perspective_id="persp-abc",
            server_id="srv-002",
            change_type="tier_changed",
            old_tier="MEDIUM",
            new_tier="HIGH",
            seen=True,
            created_at=now,
        ))
        sess.add(PerspectiveEvent(
            perspective_id="persp-abc",
            server_id="srv-003",
            change_type="left",
            old_tier="LOW",
            new_tier=None,
            seen=False,
            created_at=now,
        ))
        sess.commit()

    client = TestClient(app)

    # Test: full list
    resp = client.get("/api/perspectives/persp-abc/events?limit=10&offset=0")
    assert resp.status_code == 200, f"FAIL: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["perspective_id"] == "persp-abc"
    assert len(data["events"]) == 3
    assert data["total"] == 3
    assert data["limit"] == 10
    assert data["offset"] == 0

    # Verify ordering (newest first)
    assert data["events"][0]["change_type"] == "left"
    assert data["events"][1]["change_type"] == "tier_changed"
    assert data["events"][2]["change_type"] == "entered"

    # Test: pagination
    resp2 = client.get("/api/perspectives/persp-abc/events?limit=2&offset=0")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["events"]) == 2
    assert data2["total"] == 3

    resp3 = client.get("/api/perspectives/persp-abc/events?limit=2&offset=2")
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert len(data3["events"]) == 1

    # Test: empty perspective
    resp4 = client.get("/api/perspectives/nonexistent/events?limit=10&offset=0")
    assert resp4.status_code == 200
    data4 = resp4.json()
    assert data4["events"] == []
    assert data4["total"] == 0

    print("PASS")
