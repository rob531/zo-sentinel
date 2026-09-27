"""services/active/perspective_event_stream_api/router.py

FastAPI router for the perspective_event_stream_api service.
Provides streaming-style event endpoints for perspective membership changes.

Auth: public (no role guard on endpoints).
Multi-tenancy: all queries are scoped to the perspective's org_id derived from
the authenticated principal (via app.security.get_principal).
Data: reads PerspectiveEvent via app.db.get_session.
"""
from __future__ import annotations

import sys
from datetime import datetime
from typing import Generator, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, status
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import and_, desc
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, PerspectiveEvent, Perspective  # noqa: F401
from app.security import get_principal, Principal

router = APIRouter(prefix="/api", tags=["perspective_event_stream_api"])

# --------------------------------------------------------------------------- #
# Pydantic request/response models
# --------------------------------------------------------------------------- #


class EventItem(BaseModel):
    id: int
    perspective_id: str
    server_id: str
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: datetime

    class Config:
        from_attributes = True


class StreamResponse(BaseModel):
    perspective_id: str
    events: List[EventItem]
    next_cursor: Optional[int]
    has_more: bool


class CountResponse(BaseModel):
    perspective_id: str
    total: int
    unseen: int


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_perspective(perspective_id: str, db: Session) -> Perspective:
    p = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if p is None:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return p


def _check_org_access(perspective: Perspective, principal: Principal) -> None:
    if perspective.org_id is not None and perspective.org_id != principal.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cross-org access denied",
        )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/perspectives/{perspective_id}/events/stream",
    response_model=StreamResponse,
    status_code=status.HTTP_200_OK,
)
def stream_events(
    perspective_id: str,
    since: Optional[int] = Query(
        None,
        description="Return events with id > since (cursor-based).",
    ),
    take: int = Query(50, ge=1, le=500, description="Max events to return."),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> StreamResponse:
    """Stream events for a perspective using cursor-based pagination.

    Returns up to `take` events ordered by id ascending, starting after `since`.
    Use `next_cursor` from the response as the `since` param for the next page.
    """
    perspective = _load_perspective(perspective_id, db)
    _check_org_access(perspective, principal)

    query = db.query(PerspectiveEvent).filter(
        PerspectiveEvent.perspective_id == perspective_id
    )
    if since is not None:
        query = query.filter(PerspectiveEvent.id > since)
    query = query.order_by(PerspectiveEvent.id.asc()).limit(take + 1)

    rows = query.all()
    has_more = len(rows) > take
    if has_more:
        rows = rows[:take]

    next_cursor: Optional[int] = rows[-1].id if rows and has_more else None

    return StreamResponse(
        perspective_id=perspective_id,
        events=[EventItem.model_validate(r) for r in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get(
    "/perspectives/{perspective_id}/events/recent",
    response_model=StreamResponse,
    status_code=status.HTTP_200_OK,
)
def recent_events(
    perspective_id: str,
    limit: int = Query(20, ge=1, le=200, description="Max events to return."),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> StreamResponse:
    """Return the most recent events for a perspective (newest first).

    Unlike stream_events, this returns newest-first ordering suitable for
    dashboards and activity feeds.
    """
    perspective = _load_perspective(perspective_id, db)
    _check_org_access(perspective, principal)

    query = (
        db.query(PerspectiveEvent)
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(desc(PerspectiveEvent.id))
        .limit(limit)
    )
    rows = query.all()
    # Reverse to ascending for display consistency
    rows = list(reversed(rows))

    return StreamResponse(
        perspective_id=perspective_id,
        events=[EventItem.model_validate(r) for r in rows],
        next_cursor=None,
        has_more=False,
    )


@router.get(
    "/perspectives/{perspective_id}/events/count",
    response_model=CountResponse,
    status_code=status.HTTP_200_OK,
)
def count_events(
    perspective_id: str,
    change_type: Optional[str] = Query(
        None,
        description="Filter by change_type (e.g. 'tier_change', 'added', 'removed').",
    ),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> CountResponse:
    """Return total and unseen event counts for a perspective."""
    perspective = _load_perspective(perspective_id, db)
    _check_org_access(perspective, principal)

    query = db.query(PerspectiveEvent).filter(
        PerspectiveEvent.perspective_id == perspective_id
    )
    if change_type is not None:
        query = query.filter(PerspectiveEvent.change_type == change_type)

    total = query.count()
    unseen = query.filter(PerspectiveEvent.seen == False).count()  # noqa: E712

    return CountResponse(
        perspective_id=perspective_id,
        total=total,
        unseen=unseen,
    )


@router.post(
    "/perspectives/{perspective_id}/events/{event_id}/seen",
    status_code=status.HTTP_204_NO_CONTENT,
)
def mark_seen(
    perspective_id: str,
    event_id: int,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> None:
    """Mark a perspective event as seen."""
    perspective = _load_perspective(perspective_id, db)
    _check_org_access(perspective, principal)

    ev = (
        db.query(PerspectiveEvent)
        .filter(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.id == event_id,
        )
        .first()
    )
    if ev is None:
        raise HTTPException(status_code=404, detail="Event not found")
    ev.seen = True
    db.commit()


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    engine = sessionmaker(
        bind=StaticPool(
            create_engine(
                "sqlite:///:memory:",
                connect_args={"check_same_thread": False},
            )
        )
    )()
    # re-bind so metadata is attached
    from sqlalchemy import create_engine as _ce

    _eng = _ce("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(_eng)
    TestSession = sessionmaker(bind=_eng)

    def get_test_session() -> Generator[Session, None, None]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Seed data: 3 perspectives, events with ids 1-15
    # ------------------------------------------------------------------- #
    ts = datetime(2025, 1, 1, 0, 0, 0)
    with TestSession() as db:
        for i in range(1, 4):
            p = Perspective(
                id=f"persp-{i}",
                org_id=f"org-{i}",
                name=f"Perspective {i}",
                description="",
                facet_filters={},
                created_by="test",
            )
            db.add(p)
        db.commit()

        for i in range(1, 16):
            ev = PerspectiveEvent(
                perspective_id=f"persp-{(i - 1) % 3 + 1}",
                server_id=f"srv-{i}",
                change_type=["added", "removed", "tier_change"][i % 3],
                old_tier="low" if i % 2 == 0 else None,
                new_tier="high" if i % 2 == 1 else None,
                seen=i > 5,
                created_at=ts,
            )
            db.add(ev)
        db.commit()

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Happy-path: stream_events with cursor pagination
    # ------------------------------------------------------------------- #
    resp = client.get("/api/perspectives/persp-1/events/stream?take=3")
    assert resp.status_code == 200, f"stream failed: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["perspective_id"] == "persp-1"
    assert len(data["events"]) == 3
    assert data["has_more"] is True
    assert data["next_cursor"] == data["events"][-1]["id"]

    # Paginate
    since = data["next_cursor"]
    resp2 = client.get(f"/api/perspectives/persp-1/events/stream?since={since}&take=10")
    assert resp2.status_code == 200
    data2 = resp2.json()
    # ids should all be > since
    ids = [e["id"] for e in data2["events"]]
    assert all(i > since for i in ids), f"pagination failed: {ids} <= {since}"

    # ------------------------------------------------------------------- #
    # Happy-path: recent_events
    # ------------------------------------------------------------------- #
    resp3 = client.get("/api/perspectives/persp-1/events/recent?limit=5")
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert len(data3["events"]) == 5

    # ------------------------------------------------------------------- #
    # Happy-path: count_events
    # ------------------------------------------------------------------- #
    resp4 = client.get("/api/perspectives/persp-1/events/count")
    assert resp4.status_code == 200
    cnt = resp4.json()
    assert cnt["perspective_id"] == "persp-1"
    assert cnt["total"] == 5  # persp-1 has events with ids 1,4,7,10,13
    assert cnt["unseen"] == 2  # ids 1,4 are unseen (ids > 5 are seen)

    resp5 = client.get(
        "/api/perspectives/persp-1/events/count?change_type=tier_change"
    )
    assert resp5.status_code == 200
    cnt5 = resp5.json()
    assert cnt5["total"] == 2  # ids 3,6,9,12,15 -> 3 is tier_change (i=3, mod3=0)

    # ------------------------------------------------------------------- #
    # Happy-path: mark_seen
    # ------------------------------------------------------------------- #
    resp6 = client.post("/api/perspectives/persp-1/events/1/seen")
    assert resp6.status_code == 204

    # ------------------------------------------------------------------- #
    # Auth/404: missing auth header -> 401
    # ------------------------------------------------------------------- #
    resp7 = client.get("/api/perspectives/persp-1/events/stream")
    assert resp7.status_code == 401, f"expected 401 for missing auth, got {resp7.status_code}"

    # ------------------------------------------------------------------- #
    # 404: unknown perspective
    # ------------------------------------------------------------------- #
    from app.security import create_access_token

    token = create_access_token("user-1", "org-1", "member")
    resp8 = client.get(
        "/api/perspectives/nonexistent/events/stream",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp8.status_code == 404, f"expected 404 for unknown perspective, got {resp8.status_code}"

    print("PASS")
    sys.exit(0)
