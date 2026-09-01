"""perspective_events_feed_api.py -- Risk tier change event feed for perspectives.

GET /perspectives/{perspective_id}/events  -- tier-change events for one perspective
GET /perspectives/events/recent            -- recent events across all perspectives

Reads from perspective_events (app table, SQLAlchemy session).
No DB writes; no network I/O in request handlers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, desc
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent

router = APIRouter(prefix="/api", tags=["perspective"])

# deps: requests  # fire-and-forget audit log (not used here; kept for future extension)


class PerspectiveEventResponse(BaseModel):
    id: int
    perspective_id: str
    server_id: str
    change_type: str
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    seen: bool
    created_at: datetime


@router.get(
    "/perspectives/{perspective_id}/events",
    response_model=list[PerspectiveEventResponse],
)
def get_perspective_events(
    perspective_id: str,
    seen: Optional[bool] = Query(None, description="Filter: True=acknowledged, False=pending, None=all"),
    limit: int = Query(50, ge=1, le=500, description="Maximum events to return"),
    db: Session = Depends(get_session),
) -> list[PerspectiveEventResponse]:
    """Tier-change events for a specific perspective, newest first."""
    conditions = [PerspectiveEvent.perspective_id == perspective_id]
    if seen is not None:
        conditions.append(PerspectiveEvent.seen == seen)

    stmt = (
        select(PerspectiveEvent)
        .where(and_(*conditions))
        .order_by(desc(PerspectiveEvent.created_at))
        .limit(limit)
    )
    rows = db.execute(stmt).scalars().all()

    return [
        PerspectiveEventResponse(
            id=r.id,
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


@router.get(
    "/perspectives/events/recent",
    response_model=list[PerspectiveEventResponse],
)
def get_recent_events(
    limit: int = Query(100, ge=1, le=500, description="Maximum events to return"),
    db: Session = Depends(get_session),
) -> list[PerspectiveEventResponse]:
    """Recent tier-change events across all perspectives, newest first."""
    stmt = (
        select(PerspectiveEvent)
        .order_by(desc(PerspectiveEvent.created_at))
        .limit(limit)
    )
    rows = db.execute(stmt).scalars().all()

    return [
        PerspectiveEventResponse(
            id=r.id,
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


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    s = TS()
    s.add(PerspectiveEvent(
        id=1, perspective_id="persp1", server_id="srv1",
        change_type="tier_upgrade", old_tier="LOW", new_tier="HIGH",
        seen=False, created_at=datetime(2026, 7, 1, 12, 0, 0),
    ))
    s.add(PerspectiveEvent(
        id=2, perspective_id="persp1", server_id="srv2",
        change_type="tier_downgrade", old_tier="MEDIUM", new_tier="LOW",
        seen=True, created_at=datetime(2026, 7, 1, 11, 0, 0),
    ))
    s.add(PerspectiveEvent(
        id=3, perspective_id="persp2", server_id="srv3",
        change_type="new_server", old_tier=None, new_tier="MEDIUM",
        seen=False, created_at=datetime(2026, 7, 1, 10, 0, 0),
    ))
    s.add(PerspectiveEvent(
        id=4, perspective_id="persp1", server_id="srv4",
        change_type="removed", old_tier="CRITICAL", new_tier=None,
        seen=False, created_at=datetime(2026, 7, 1, 9, 0, 0),
    ))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    # Happy path: get events for perspective
    r = c.get("/api/perspectives/persp1/events")
    assert r.status_code == 200, r.text
    j = r.json()
    assert isinstance(j, list), j
    assert len(j) == 3, f"Expected 3 events for persp1, got {len(j)}"
    # Verify keys
    e = j[0]
    for key in ("id", "perspective_id", "server_id", "change_type", "old_tier", "new_tier", "seen", "created_at"):
        assert key in e, f"Missing key {key} in {e}"
    # Ordered by created_at desc
    assert j[0]["change_type"] == "tier_upgrade", j
    assert j[1]["change_type"] == "tier_downgrade", j
    assert j[2]["change_type"] == "removed", j
    # change_type in allowed set
    for ev in j:
        assert ev["change_type"] in {"tier_upgrade", "tier_downgrade", "new_server", "removed"}, ev
    # created_at is ISO8601
    assert "T" in j[0]["created_at"], f"Expected ISO8601, got {j[0]['created_at']}"

    # Filter by seen=False (pending)
    r2 = c.get("/api/perspectives/persp1/events?seen=false")
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert len(j2) == 2, j2
    assert all(not e["seen"] for e in j2), j2

    # Filter by seen=True (acknowledged)
    r3 = c.get("/api/perspectives/persp1/events?seen=true")
    assert r3.status_code == 200, r3.text
    j3 = r3.json()
    assert len(j3) == 1, j3
    assert j3[0]["seen"] is True, j3

    # Limit parameter
    r4 = c.get("/api/perspectives/persp1/events?limit=2")
    assert r4.status_code == 200, r4.text
    j4 = r4.json()
    assert len(j4) == 2, j4

    # Recent events across all perspectives
    r5 = c.get("/api/perspectives/events/recent")
    assert r5.status_code == 200, r5.text
    j5 = r5.json()
    assert isinstance(j5, list), j5
    assert len(j5) == 4, f"Expected 4 total events, got {len(j5)}"
    assert j5[0]["perspective_id"] == "persp1", j5  # newest first

    # Recent events with limit
    r6 = c.get("/api/perspectives/events/recent?limit=2")
    assert r6.status_code == 200, r6.text
    j6 = r6.json()
    assert len(j6) == 2, j6

    # Edge case: non-existent perspective returns empty list
    r7 = c.get("/api/perspectives/nonexistent/events")
    assert r7.status_code == 200, r7.text
    j7 = r7.json()
    assert j7 == [], j7

    print("PASS")
