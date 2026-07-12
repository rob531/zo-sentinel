"""Perspective event stream API: GET /perspectives/events.

Streams recent perspective tier-change events from the perspective_events table.
Ordered by created_at descending; supports filtering by perspective_id and server_id
with pagination (limit/offset).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, desc
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent

router = APIRouter(prefix="/api", tags=["perspective"])


class PerspectiveEventResponse(BaseModel):
    perspective_id: str
    server_id: str
    change_type: str
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    seen: bool
    created_at: datetime


class PerspectiveEventListResponse(BaseModel):
    events: List[PerspectiveEventResponse]
    count: int
    limit: int
    offset: int


@router.get("/perspectives/events", response_model=PerspectiveEventListResponse)
def get_perspective_events(
    perspective_id: Optional[str] = Query(None, description="Filter by perspective ID"),
    server_id: Optional[str] = Query(None, description="Filter by server ID"),
    limit: int = Query(50, ge=1, le=500, description="Maximum events to return"),
    offset: int = Query(0, ge=0, description="Number of events to skip"),
    db: Session = Depends(get_session),
) -> PerspectiveEventListResponse:
    """Stream recent perspective tier-change events from perspective_events.

    Returns events ordered by created_at descending. Use perspective_id and/or
    server_id to filter; pagination via limit and offset.
    """
    conditions = []
    if perspective_id:
        conditions.append(PerspectiveEvent.perspective_id == perspective_id)
    if server_id:
        conditions.append(PerspectiveEvent.server_id == server_id)

    stmt = select(PerspectiveEvent)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.order_by(desc(PerspectiveEvent.created_at)).offset(offset).limit(limit)

    rows = db.execute(stmt).scalars().all()

    events = [
        PerspectiveEventResponse(
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

    return PerspectiveEventListResponse(
        events=events,
        count=len(events),
        limit=limit,
        offset=offset,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    s = TS()
    s.add(PerspectiveEvent(id=1, perspective_id="persp1", server_id="srv1",
                            change_type="tier_changed", old_tier="LOW", new_tier="HIGH",
                            seen=False, created_at=datetime(2026, 7, 1, 12, 0, 0)))
    s.add(PerspectiveEvent(id=2, perspective_id="persp1", server_id="srv2",
                            change_type="entered", old_tier=None, new_tier="MEDIUM",
                            seen=True, created_at=datetime(2026, 7, 1, 11, 0, 0)))
    s.add(PerspectiveEvent(id=3, perspective_id="persp2", server_id="srv1",
                            change_type="left", old_tier="CRITICAL", new_tier=None,
                            seen=False, created_at=datetime(2026, 7, 1, 10, 0, 0)))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    # Happy path: default limit=50, ordered by created_at desc
    r = c.get("/api/perspectives/events")
    assert r.status_code == 200, r.text
    j = r.json()
    assert "events" in j, j
    assert len(j["events"]) == 3, j
    # Verify expected keys
    e = j["events"][0]
    for key in ("perspective_id", "server_id", "change_type", "old_tier", "new_tier", "seen", "created_at"):
        assert key in e, f"Missing key {key} in {e}"
    # Ordered by created_at desc
    assert j["events"][0]["perspective_id"] == "persp1", j
    assert j["events"][1]["perspective_id"] == "persp1", j
    assert j["events"][2]["perspective_id"] == "persp2", j

    # Filter by perspective_id
    r2 = c.get("/api/perspectives/events?perspective_id=persp1")
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["count"] == 2, j2
    assert all(e["perspective_id"] == "persp1" for e in j2["events"]), j2

    # Filter by server_id
    r3 = c.get("/api/perspectives/events?server_id=srv1")
    assert r3.status_code == 200, r3.text
    j3 = r3.json()
    assert j3["count"] == 2, j3
    assert all(e["server_id"] == "srv1" for e in j3["events"]), j3

    # Pagination: limit + offset
    r4 = c.get("/api/perspectives/events?limit=1&offset=1")
    assert r4.status_code == 200, r4.text
    j4 = r4.json()
    assert j4["count"] == 1, j4
    assert j4["offset"] == 1
    assert j4["limit"] == 1

    # Edge case: no events
    r5 = c.get("/api/perspectives/events?perspective_id=nonexistent")
    assert r5.status_code == 200, r5.text
    j5 = r5.json()
    assert j5["events"] == [], j5
    assert j5["count"] == 0, j5

    print("PASS")
