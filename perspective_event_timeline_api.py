"""Perspective event timeline API: GET /servers/{server_id}/perspective-timeline.

Returns a chronological array of risk-tier change events for a given server,
joining perspective_events with perspective_snapshots (when available) and
the perspective name from perspectives.
"""
from __future__ import annotations

from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, or_, and_, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent, PerspectiveSnapshot, Perspective

router = APIRouter(prefix="/api", tags=["perspective"])


class TimelineEvent(BaseModel):
    event_id: int
    perspective_id: str
    perspective_name: Optional[str] = None
    change_type: str
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    seen: bool
    created_at: datetime


class PerspectiveTimelineResponse(BaseModel):
    server_id: str
    events: List[TimelineEvent]


@router.get("/servers/{server_id}/perspective-timeline", response_model=PerspectiveTimelineResponse)
def get_perspective_timeline(
    server_id: str,
    db: Session = Depends(get_session),
) -> PerspectiveTimelineResponse:
    """Chronological array of risk-tier change events for a server.

    Reads from perspective_events (change history) and perspective_snapshots
    (point-in-time membership snapshots when available). Joins to the
    perspectives table to surface the perspective_name for each event.
    """
    # Fetch all events for this server, ordered chronologically
    events = db.execute(
        select(PerspectiveEvent)
        .where(PerspectiveEvent.server_id == server_id)
        .order_by(PerspectiveEvent.created_at.asc())
    ).scalars().all()

    if not events:
        return PerspectiveTimelineResponse(server_id=server_id, events=[])

    # Collect perspective IDs to batch-fetch names
    perspective_ids = list({e.perspective_id for e in events})
    perspectives = db.execute(
        select(Perspective.id, Perspective.name)
        .where(Perspective.id.in_(perspective_ids))
    ).all()
    perspective_names = {p.id: p.name for p in perspectives}

    timeline_events = []
    for ev in events:
        timeline_events.append(TimelineEvent(
            event_id=ev.id,
            perspective_id=ev.perspective_id,
            perspective_name=perspective_names.get(ev.perspective_id),
            change_type=ev.change_type,
            old_tier=ev.old_tier,
            new_tier=ev.new_tier,
            seen=ev.seen,
            created_at=ev.created_at,
        ))

    return PerspectiveTimelineResponse(server_id=server_id, events=timeline_events)


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

    # Seed test data: two perspectives, events, and snapshots
    s = TS()
    s.add(Perspective(id="persp1", name="High Risk Servers", description="",
                      facet_filters={}, created_by="admin"))
    s.add(Perspective(id="persp2", name="All Auth Servers", description="",
                      facet_filters={}, created_by="admin"))
    # Event: tier_changed from LOW to HIGH
    s.add(PerspectiveEvent(id=1, perspective_id="persp1", server_id="srv_timeline",
                            change_type="tier_changed", old_tier="LOW", new_tier="HIGH",
                            seen=False, created_at=datetime(2026, 7, 1, 10, 0, 0)))
    # Event: entered at HIGH
    s.add(PerspectiveEvent(id=2, perspective_id="persp1", server_id="srv_timeline",
                            change_type="entered", old_tier=None, new_tier="HIGH",
                            seen=False, created_at=datetime(2026, 7, 1, 9, 0, 0)))
    # Snapshot for persp1
    s.add(PerspectiveSnapshot(id=1, perspective_id="persp1",
                               membership={"srv_timeline": "HIGH", "srv2": "MEDIUM"},
                               taken_at=datetime(2026, 7, 1, 9, 0, 0)))
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

    # Happy path: server with events
    r = c.get("/api/servers/srv_timeline/perspective-timeline")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["server_id"] == "srv_timeline", j
    assert len(j["events"]) == 2, j
    # Events should be chronological (earliest first)
    assert j["events"][0]["change_type"] == "entered", j
    assert j["events"][0]["new_tier"] == "HIGH", j
    assert j["events"][1]["change_type"] == "tier_changed", j
    assert j["events"][1]["old_tier"] == "LOW", j
    assert j["events"][1]["new_tier"] == "HIGH", j
    # perspective_name should be present from the join
    names = {e["perspective_id"]: e["perspective_name"] for e in j["events"]}
    assert names.get("persp1") == "High Risk Servers", names

    # Edge case: server with no events
    r2 = c.get("/api/servers/nonexistent_server/perspective-timeline")
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["server_id"] == "nonexistent_server", j2
    assert j2["events"] == [], j2

    print("PASS")
