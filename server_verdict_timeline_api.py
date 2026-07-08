"""server_verdict_timeline_api.py -- verdict-change history for a server.

Exposes GET /servers/{server_id}/verdict-timeline and GET /servers/{server_id}/timeline,
both reading from the perspective_events table (change_type, old_tier, new_tier, seen,
created_at) ordered by created_at DESC. Supports ?limit=N.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent

router = APIRouter(prefix="/servers", tags=["verdict"])


class TimelineEvent(BaseModel):
    change_type: str
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    seen: bool = False
    created_at: datetime


class TimelineResponse(BaseModel):
    server_id: str
    events: list[TimelineEvent]


@router.get("/{server_id}/verdict-timeline", response_model=TimelineResponse)
def get_verdict_timeline(
    server_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> TimelineResponse:
    """Verdict-change history for a server, newest first."""
    rows = (
        db.execute(
            select(PerspectiveEvent)
            .where(PerspectiveEvent.server_id == server_id)
            .order_by(desc(PerspectiveEvent.created_at))
            .limit(limit)
        )
        .scalars()
        .all()
    )
    events = [
        TimelineEvent(
            change_type=r.change_type,
            old_tier=r.old_tier,
            new_tier=r.new_tier,
            seen=r.seen,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return TimelineResponse(server_id=server_id, events=events)


@router.get("/{server_id}/timeline", response_model=TimelineResponse)
def get_timeline(
    server_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> TimelineResponse:
    """Alias for /verdict-timeline (same data)."""
    return get_verdict_timeline(server_id=server_id, limit=limit, db=db)


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
    s.add(
        PerspectiveEvent(
            perspective_id="p1",
            server_id="srv1",
            change_type="tier_changed",
            old_tier="LOW",
            new_tier="MEDIUM",
            seen=True,
        )
    )
    s.add(
        PerspectiveEvent(
            perspective_id="p2",
            server_id="srv1",
            change_type="entered",
            old_tier=None,
            new_tier="LOW",
            seen=False,
        )
    )
    s.add(
        PerspectiveEvent(
            perspective_id="p3",
            server_id="srv2",
            change_type="left",
            old_tier="HIGH",
            new_tier=None,
            seen=True,
        )
    )
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

    # Happy path: srv1 has 2 events
    r = c.get("/servers/srv1/verdict-timeline")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["server_id"] == "srv1"
    assert len(j["events"]) == 2
    assert j["events"][0]["change_type"] == "tier_changed"
    assert j["events"][0]["old_tier"] == "LOW"
    assert j["events"][0]["new_tier"] == "MEDIUM"
    assert j["events"][1]["change_type"] == "entered"
    assert j["events"][1]["old_tier"] is None
    assert j["events"][1]["new_tier"] == "LOW"

    # Alias endpoint
    r2 = c.get("/servers/srv1/timeline")
    assert r2.status_code == 200
    assert r2.json()["events"] == j["events"]

    # Limit param
    r3 = c.get("/servers/srv1/verdict-timeline?limit=1")
    assert r3.status_code == 200
    assert len(r3.json()["events"]) == 1

    # Server with no events
    r4 = c.get("/servers/srv999/verdict-timeline")
    assert r4.status_code == 200
    assert r4.json()["events"] == []

    print("PASS")