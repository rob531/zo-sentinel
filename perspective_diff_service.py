"""perspective_diff_service.py -- trust-diff: the alerting attach point.

The reason Perspectives exist as a UNIT (FATHER + the product concept): "alert
me when anything in MY view changes tier". snapshot_perspective() persists the
perspective's current membership {server_id: risk_tier}; diff_perspective()
compares live membership vs the last snapshot -> {entered, left, tier_changed}
and queues ONE in-app notification row per change (PerspectiveEvent). No
external connectors (deliberately parked -- per-customer credential
segmentation), so webhooks later attach here without rework.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot
from perspective_query_api import query_perspective_servers
from verdict_breakdown_api import Principal, get_principal, require_admin

router = APIRouter(prefix="/api", tags=["perspectives"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def current_membership(db: Session, perspective: Perspective) -> Dict[str, str]:
    """Live {server_id: risk_tier} for a perspective (unpaginated, bounded by
    the corpus)."""
    servers, _total, _fc = query_perspective_servers(
        db, perspective.facet_filters or {}, page=1, page_size=10 ** 9)
    return {s["server_id"]: (s["risk_tier"] or "") for s in servers}


def snapshot_perspective(db: Session, perspective_id: str) -> PerspectiveSnapshot:
    p = db.get(Perspective, perspective_id)
    if p is None:
        raise ValueError("perspective not found")
    snap = PerspectiveSnapshot(perspective_id=perspective_id, taken_at=_now(),
                               membership=current_membership(db, p))
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def last_snapshot(db: Session, perspective_id: str) -> Optional[PerspectiveSnapshot]:
    return db.execute(
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.desc(), PerspectiveSnapshot.id.desc())
        .limit(1)
    ).scalars().first()


def diff_perspective(db: Session, perspective_id: str,
                     queue_events: bool = True) -> dict:
    """Diff live membership vs the last snapshot. Queues PerspectiveEvent rows
    (one per change) unless queue_events=False. No snapshot yet -> everything
    is baseline, no events (first snapshot defines the reference)."""
    p = db.get(Perspective, perspective_id)
    if p is None:
        raise ValueError("perspective not found")
    prior = last_snapshot(db, perspective_id)
    live = current_membership(db, p)
    if prior is None:
        return {"baseline": True, "entered": [], "left": [], "tier_changed": [],
                "live_count": len(live)}
    old: Dict[str, str] = dict(prior.membership or {})

    entered = sorted(set(live) - set(old))
    left = sorted(set(old) - set(live))
    tier_changed: List[dict] = [
        {"server_id": sid, "old": old[sid], "new": live[sid]}
        for sid in sorted(set(live) & set(old)) if old[sid] != live[sid]]

    if queue_events:
        for sid in entered:
            db.add(PerspectiveEvent(perspective_id=perspective_id, server_id=sid,
                                    change_type="entered", new_tier=live[sid],
                                    created_at=_now()))
        for sid in left:
            db.add(PerspectiveEvent(perspective_id=perspective_id, server_id=sid,
                                    change_type="left", old_tier=old[sid],
                                    created_at=_now()))
        for ch in tier_changed:
            db.add(PerspectiveEvent(perspective_id=perspective_id,
                                    server_id=ch["server_id"],
                                    change_type="tier_changed",
                                    old_tier=ch["old"], new_tier=ch["new"],
                                    created_at=_now()))
        db.commit()

    return {"baseline": False, "entered": entered, "left": left,
            "tier_changed": tier_changed, "live_count": len(live),
            "snapshot_at": prior.taken_at.isoformat() if prior.taken_at else None}


@router.post("/perspectives/{perspective_id}/snapshot")
def take_snapshot(perspective_id: str, db: Session = Depends(get_session),
                  principal: Principal = Depends(require_admin)) -> dict:
    try:
        snap = snapshot_perspective(db, perspective_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return {"snapshot_id": snap.id, "taken_at": snap.taken_at.isoformat(),
            "members": len(snap.membership or {})}


@router.get("/perspectives/{perspective_id}/diff")
def get_diff(perspective_id: str, db: Session = Depends(get_session),
             principal: Principal = Depends(get_principal)) -> dict:
    try:
        return diff_perspective(db, perspective_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Perspective not found")


@router.get("/perspectives/{perspective_id}/events")
def get_events(perspective_id: str, db: Session = Depends(get_session),
               principal: Principal = Depends(get_principal)) -> dict:
    rows = db.execute(
        select(PerspectiveEvent)
        .where(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(PerspectiveEvent.created_at.desc()).limit(200)
    ).scalars()
    return {"events": [{
        "id": e.id, "server_id": e.server_id, "change_type": e.change_type,
        "old_tier": e.old_tier, "new_tier": e.new_tier,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in rows]}


if __name__ == "__main__":
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import McpServerRegistry as R
    from perspective_model import create_perspective
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add_all([R(server_id="s1", risk_tier="HIGH", verdict="HIGH", registry_source="github"),
               R(server_id="s2", risk_tier="HIGH", verdict="HIGH", registry_source="npm")])
    s.commit()
    p = create_perspective(s, "highs", {"risk_tier": ["HIGH"]}, "admin_1")

    snapshot_perspective(s, p.id)
    # simulate one tier change (s1 leaves the HIGH view) and one departure (s2 deleted)
    s1 = s.get(R, "s1"); s1.risk_tier = "MEDIUM"
    s.delete(s.get(R, "s2"))
    s.commit()
    d = diff_perspective(s, p.id)
    assert d["left"] == ["s1", "s2"], d   # both left the HIGH membership
    events = s.execute(select(PerspectiveEvent)).scalars().all()
    assert len(events) == 2 and all(e.change_type == "left" for e in events)

    # tier change WITHIN the view: widen the filter, re-snapshot, then change tier
    from perspective_model import update_perspective
    update_perspective(s, p.id, facet_filters={"risk_tier": ["HIGH", "MEDIUM"]})
    snapshot_perspective(s, p.id)
    s1.risk_tier = "HIGH"; s.commit()
    d2 = diff_perspective(s, p.id)
    assert d2["tier_changed"] == [{"server_id": "s1", "old": "MEDIUM", "new": "HIGH"}]
    print("PASS")
