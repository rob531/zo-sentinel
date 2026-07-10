"""server_tier_change_events_api.py -- Tier-change event feed from perspective_events.

Reads rows from the perspective_events table (change_type='tier_change') via
the app SQLAlchemy session. Read-only; no DB writes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent

router = APIRouter(prefix="/api", tags=["events"])


class TierChangeEvent(BaseModel):
    server_id: str
    perspective_id: str
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    seen: bool = False
    created_at: datetime


class TierChangeEventList(BaseModel):
    events: List[TierChangeEvent]
    count: int
    limit: int
    offset: int


@router.get("/tier-change-events", response_model=TierChangeEventList)
def list_tier_change_events(
    server_id: Optional[str] = Query(None, description="Filter by server_id"),
    perspective_id: Optional[str] = Query(None, description="Filter by perspective_id"),
    start_date: Optional[datetime] = Query(None, description="Filter events on or after this datetime"),
    end_date: Optional[datetime] = Query(None, description="Filter events on or before this datetime"),
    limit: int = Query(50, ge=1, le=500, description="Max rows to return"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
    db: Session = Depends(get_session),
) -> TierChangeEventList:
    """Return tier-change events from perspective_events filtered by the given params."""
    conditions = [PerspectiveEvent.change_type == "tier_change"]
    if server_id is not None:
        conditions.append(PerspectiveEvent.server_id == server_id)
    if perspective_id is not None:
        conditions.append(PerspectiveEvent.perspective_id == perspective_id)
    if start_date is not None:
        conditions.append(PerspectiveEvent.created_at >= start_date)
    if end_date is not None:
        conditions.append(PerspectiveEvent.created_at <= end_date)

    count_stmt = select(PerspectiveEvent).where(and_(*conditions))
    count = len(db.execute(count_stmt).scalars().all())

    stmt = (
        select(PerspectiveEvent)
        .where(and_(*conditions))
        .order_by(PerspectiveEvent.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = db.execute(stmt).scalars().all()

    events = [
        TierChangeEvent(
            server_id=r.server_id,
            perspective_id=r.perspective_id,
            old_tier=r.old_tier,
            new_tier=r.new_tier,
            seen=r.seen,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return TierChangeEventList(events=events, count=count, limit=limit, offset=offset)


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

    # Use ONE session for all seeding so StaticPool sees everything committed
    shared = TS()
    shared.add(PerspectiveEvent(id=1, perspective_id="persp1", server_id="srv1",
                                 change_type="tier_change", old_tier="LOW",
                                 new_tier="HIGH", seen=False))
    shared.add(PerspectiveEvent(id=2, perspective_id="persp1", server_id="srv2",
                                 change_type="tier_change", old_tier="MEDIUM",
                                 new_tier="CRITICAL", seen=True))
    shared.add(PerspectiveEvent(id=3, perspective_id="persp2", server_id="srv1",
                                 change_type="tier_change", old_tier="LOW",
                                 new_tier="MEDIUM", seen=False))
    shared.add(PerspectiveEvent(id=4, perspective_id="persp1", server_id="srv3",
                                 change_type="other_change", old_tier="LOW",
                                 new_tier="HIGH", seen=False))
    shared.commit()
    shared.close()

    app = FastAPI()
    app.include_router(router)

    # Single shared session for test requests too
    _test_sess = TS()

    def _override_session():
        try:
            yield _test_sess
        finally:
            pass

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    # Happy path: server_id filter
    r = c.get("/api/tier-change-events?server_id=srv1")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["count"] == 2, f"expected 2 srv1 events, got {j}"
    assert all(e["server_id"] == "srv1" for e in j["events"]), j

    # Filter by perspective_id (persp1 has 3 tier_change rows: srv1, srv2, srv3's other_change is excluded)
    r2 = c.get("/api/tier-change-events?perspective_id=persp1")
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["count"] == 2, f"expected 2 persp1 tier_change events, got {j2}"

    # Filter by server_id + perspective_id (only srv1+persp1 tier_change)
    r3 = c.get("/api/tier-change-events?server_id=srv1&perspective_id=persp1")
    assert r3.status_code == 200, r3.text
    j3 = r3.json()
    assert j3["count"] == 1, f"expected 1 combined-filter event, got {j3}"
    assert j3["events"][0]["old_tier"] == "LOW"
    assert j3["events"][0]["new_tier"] == "HIGH"

    # No matching rows
    r4 = c.get("/api/tier-change-events?server_id=nonexistent")
    assert r4.status_code == 200, r4.text
    j4 = r4.json()
    assert j4["count"] == 0, j4
    assert j4["events"] == [], j4

    # Pagination
    r5 = c.get("/api/tier-change-events?limit=1&offset=0")
    assert r5.status_code == 200, r5.text
    j5 = r5.json()
    assert len(j5["events"]) == 1, j5
    assert j5["count"] == 3, j5
    assert j5["limit"] == 1
    assert j5["offset"] == 0

    print("PASS")
