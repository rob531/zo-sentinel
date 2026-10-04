# deps: fastapi, sqlalchemy, pydantic
"""Perspective Event API

Public endpoints for querying perspective event history and snapshot context.
Reads from perspective_events and perspective_snapshots tables via the
standard app DB session dependency.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent, PerspectiveSnapshot

router = APIRouter(prefix="/api", tags=["perspective_event_api"])


# --- Request / Response models ---------------------------------------------

class EventResponse(BaseModel):
    id: int
    perspective_id: str
    server_id: str
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: datetime

    class Config:
        orm_mode = True


class SnapshotContext(BaseModel):
    snapshot_id: int
    perspective_id: str
    taken_at: datetime
    membership: Optional[dict]

    class Config:
        orm_mode = True


class EventsWithContextResponse(BaseModel):
    perspective_id: str
    events: list[EventResponse]
    total: int
    limit: int
    offset: int
    snapshot_context: Optional[SnapshotContext] = None


class UnseenCountResponse(BaseModel):
    perspective_id: str
    unseen_count: int


# --- Endpoints ------------------------------------------------------------

@router.get(
    "/perspectives/{perspective_id}/events",
    response_model=EventsWithContextResponse,
)
def list_perspective_events(
    perspective_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_snapshot: bool = Query(default=False),
    db: Session = Depends(get_session),
) -> EventsWithContextResponse:
    """Return paginated event history for a perspective, newest first.

    Optionally attaches the most recent snapshot context.
    """
    total = (
        db.query(func.count(PerspectiveEvent.id))
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

    events = [EventResponse.model_validate(r) for r in rows]

    snapshot_context: Optional[SnapshotContext] = None
    if include_snapshot:
        latest_snapshot = (
            db.query(PerspectiveSnapshot)
            .filter(PerspectiveSnapshot.perspective_id == perspective_id)
            .order_by(desc(PerspectiveSnapshot.taken_at))
            .first()
        )
        if latest_snapshot:
            snapshot_context = SnapshotContext.model_validate(latest_snapshot)

    return EventsWithContextResponse(
        perspective_id=perspective_id,
        events=events,
        total=total,
        limit=limit,
        offset=offset,
        snapshot_context=snapshot_context,
    )


@router.get(
    "/perspectives/{perspective_id}/events/unseen-count",
    response_model=UnseenCountResponse,
)
def get_unseen_count(
    perspective_id: str,
    db: Session = Depends(get_session),
) -> UnseenCountResponse:
    """Return the count of unseen events for a perspective."""
    count = (
        db.query(func.count(PerspectiveEvent.id))
        .filter(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.seen == False,  # noqa: E712
        )
        .scalar()
    )
    return UnseenCountResponse(perspective_id=perspective_id, unseen_count=count)


@router.patch(
    "/perspectives/{perspective_id}/events/mark-seen",
    status_code=status.HTTP_204_NO_CONTENT,
)
def mark_events_seen(
    perspective_id: str,
    event_ids: list[int] | None = Query(default=None),
    db: Session = Depends(get_session),
) -> None:
    """Mark events as seen. If event_ids is omitted, marks ALL unseen events."""
    q = db.query(PerspectiveEvent).filter(
        PerspectiveEvent.perspective_id == perspective_id,
        PerspectiveEvent.seen == False,  # noqa: E712
    )
    if event_ids is not None:
        q = q.filter(PerspectiveEvent.id.in_(event_ids))
    q.update({PerspectiveEvent.seen: True}, synchronize_session=False)
    db.commit()


# --- Self-test ------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from contextlib import contextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base

    sqlite_engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=sqlite_engine)
    TestSession = sessionmaker(bind=sqlite_engine, expire_on_commit=False)

    @contextmanager
    def get_test_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    with TestSession() as s:
        s.add_all([
            PerspectiveEvent(
                perspective_id="pid-test",
                server_id="srv-001",
                change_type="entered",
                old_tier=None,
                new_tier="medium",
                seen=False,
                created_at=datetime(2024, 1, 1, 10, 0, 0),
            ),
            PerspectiveEvent(
                perspective_id="pid-test",
                server_id="srv-002",
                change_type="tier_changed",
                old_tier="low",
                new_tier="high",
                seen=False,
                created_at=datetime(2024, 1, 2, 11, 0, 0),
            ),
            PerspectiveSnapshot(
                perspective_id="pid-test",
                taken_at=datetime(2024, 1, 1, 9, 0, 0),
                membership={"srv-001": "medium", "srv-002": "high"},
            ),
        ])
        s.commit()

    client = TestClient(app)

    # Test 1: list events
    resp = client.get("/api/perspectives/pid-test/events?limit=10&offset=0")
    if resp.status_code != 200:
        print(f"FAIL: list events returned {resp.status_code}")
        sys.exit(1)
    data = resp.json()
    if "events" not in data or len(data["events"]) != 2:
        print(f"FAIL: expected 2 events, got {len(data.get('events', []))}")
        sys.exit(1)
    if data["total"] != 2:
        print(f"FAIL: total should be 2, got {data['total']}")
        sys.exit(1)

    # Test 2: unseen count
    resp2 = client.get("/api/perspectives/pid-test/events/unseen-count")
    if resp2.status_code != 200:
        print(f"FAIL: unseen count returned {resp2.status_code}")
        sys.exit(1)
    if resp2.json()["unseen_count"] != 2:
        print(f"FAIL: unseen count should be 2, got {resp2.json()['unseen_count']}")
        sys.exit(1)

    # Test 3: mark all seen
    resp3 = client.patch("/api/perspectives/pid-test/events/mark-seen")
    if resp3.status_code != 204:
        print(f"FAIL: mark-seen returned {resp3.status_code}")
        sys.exit(1)

    # Verify unseen count drops to 0
    resp4 = client.get("/api/perspectives/pid-test/events/unseen-count")
    if resp4.json()["unseen_count"] != 0:
        print(f"FAIL: unseen count should be 0 after mark-seen, got {resp4.json()['unseen_count']}")
        sys.exit(1)

    # Test 4: with snapshot context
    resp5 = client.get("/api/perspectives/pid-test/events?include_snapshot=true")
    if resp5.status_code != 200:
        print(f"FAIL: list with snapshot returned {resp5.status_code}")
        sys.exit(1)
    if resp5.json()["snapshot_context"] is None:
        print("FAIL: snapshot_context should not be None")
        sys.exit(1)

    print("PASS")
