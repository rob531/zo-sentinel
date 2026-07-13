"""perspective_snapshot_comparison_api.py -- Compare two perspective snapshots.

GET /perspectives/{perspective_id}/snapshots/compare compares two snapshots and surfaces
added/removed/changed servers and their tier transitions.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveSnapshot, Perspective

router = APIRouter(prefix="/api", tags=["perspectives"])


class ServerChange(BaseModel):
    server_id: str
    old_tier: str
    new_tier: str


class SnapshotComparisonResponse(BaseModel):
    perspective_id: str
    snapshot_a: int
    snapshot_b: int
    taken_at_a: str
    taken_at_b: str
    servers_added: list[str]
    servers_removed: list[str]
    servers_changed: list[ServerChange]
    servers_unchanged: list[str]


def _membership_map(membership) -> dict:
    """Parse membership JSON: list of {server_id, risk_tier} objects -> {server_id: risk_tier}."""
    if membership is None:
        return {}
    if isinstance(membership, dict):
        return membership
    if isinstance(membership, str):
        membership = json.loads(membership)
    if isinstance(membership, list):
        return {item["server_id"]: item["risk_tier"] for item in membership}
    return {}


@router.get("/perspectives/{perspective_id}/snapshots/compare",
            response_model=SnapshotComparisonResponse)
def compare_snapshots(
        perspective_id: str,
        snapshot_a: int,
        snapshot_b: int,
        db: Session = Depends(get_session)) -> SnapshotComparisonResponse:
    """Compare two snapshots of a perspective and return added/removed/changed/unchanged servers."""
    # Verify perspective exists
    persp = db.execute(
        select(Perspective).where(Perspective.id == perspective_id)
    ).scalar_one_or_none()
    if persp is None:
        raise HTTPException(status_code=404, detail=f"Perspective {perspective_id!r} not found")

    # Load snapshot A
    snap_a = db.execute(
        select(PerspectiveSnapshot).where(
            PerspectiveSnapshot.id == snapshot_a,
            PerspectiveSnapshot.perspective_id == perspective_id
        )
    ).scalar_one_or_none()
    if snap_a is None:
        raise HTTPException(status_code=404,
                            detail=f"Snapshot {snapshot_a} not found for perspective {perspective_id!r}")

    # Load snapshot B
    snap_b = db.execute(
        select(PerspectiveSnapshot).where(
            PerspectiveSnapshot.id == snapshot_b,
            PerspectiveSnapshot.perspective_id == perspective_id
        )
    ).scalar_one_or_none()
    if snap_b is None:
        raise HTTPException(status_code=404,
                            detail=f"Snapshot {snapshot_b} not found for perspective {perspective_id!r}")

    # membership can be a list of {server_id, risk_tier} OR a dict
    map_a = _membership_map(snap_a.membership)
    map_b = _membership_map(snap_b.membership)
    ids_a = set(map_a.keys())
    ids_b = set(map_b.keys())

    servers_added = sorted(ids_b - ids_a)
    servers_removed = sorted(ids_a - ids_b)
    ids_both = ids_a & ids_b

    # Tier changes: server present in both but different risk_tier
    servers_changed = [
        ServerChange(server_id=sid, old_tier=map_a[sid], new_tier=map_b[sid])
        for sid in sorted(ids_both) if map_a[sid] != map_b[sid]
    ]
    servers_unchanged = sorted(sid for sid in ids_both if map_a[sid] == map_b[sid])

    taken_at_a = snap_a.taken_at.isoformat() if snap_a.taken_at else ""
    taken_at_b = snap_b.taken_at.isoformat() if snap_b.taken_at else ""

    return SnapshotComparisonResponse(
        perspective_id=perspective_id,
        snapshot_a=snapshot_a,
        snapshot_b=snapshot_b,
        taken_at_a=taken_at_a,
        taken_at_b=taken_at_b,
        servers_added=servers_added,
        servers_removed=servers_removed,
        servers_changed=servers_changed,
        servers_unchanged=servers_unchanged,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base
    from datetime import datetime

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    s = TS()
    # Seed perspective
    s.add(Perspective(id="perm_001", name="Permissive",
                      description="", facet_filters={}, created_by="test"))
    # Snapshot A: servers s1, s2, s3 (unchanged same tier), s4 (removed), s5 (tier changed)
    s.add(PerspectiveSnapshot(id=1, perspective_id="perm_001",
                              membership=[{"server_id": "s1", "risk_tier": "HIGH"},
                                          {"server_id": "s2", "risk_tier": "MEDIUM"},
                                          {"server_id": "s3", "risk_tier": "LOW"},
                                          {"server_id": "s4", "risk_tier": "HIGH"},
                                          {"server_id": "s5", "risk_tier": "MEDIUM"}],
                              taken_at=datetime(2026, 1, 1, 0, 0, 0)))
    # Snapshot B: s1, s2, s3 unchanged, s5 tier changed, s6/s7 added, s4 removed
    s.add(PerspectiveSnapshot(id=2, perspective_id="perm_001",
                              membership=[{"server_id": "s1", "risk_tier": "HIGH"},
                                          {"server_id": "s2", "risk_tier": "MEDIUM"},
                                          {"server_id": "s3", "risk_tier": "LOW"},
                                          {"server_id": "s5", "risk_tier": "CRITICAL"},
                                          {"server_id": "s6", "risk_tier": "LOW"},
                                          {"server_id": "s7", "risk_tier": "HIGH"}],
                              taken_at=datetime(2026, 6, 1, 0, 0, 0)))
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

    # Happy path: compare A vs B
    r = c.get("/api/perspectives/perm_001/snapshots/compare?snapshot_a=1&snapshot_b=2")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["perspective_id"] == "perm_001"
    assert set(j["servers_added"]) == {"s6", "s7"}, f"servers_added={j['servers_added']}"
    assert set(j["servers_removed"]) == {"s4"}, f"servers_removed={j['servers_removed']}"
    changed = {ch["server_id"]: ch for ch in j["servers_changed"]}
    assert "s5" in changed, f"servers_changed={j['servers_changed']}"
    assert changed["s5"]["old_tier"] == "MEDIUM"
    assert changed["s5"]["new_tier"] == "CRITICAL"
    assert set(j["servers_unchanged"]) == {"s1", "s2", "s3"}, f"servers_unchanged={j['servers_unchanged']}"
    assert j["taken_at_a"] == "2026-01-01T00:00:00", j["taken_at_a"]
    assert j["taken_at_b"] == "2026-06-01T00:00:00", j["taken_at_b"]

    # Edge: missing perspective
    r2 = c.get("/api/perspectives/nope/snapshots/compare?snapshot_a=1&snapshot_b=2")
    assert r2.status_code == 404, r2.text

    # Edge: missing snapshot
    r3 = c.get("/api/perspectives/perm_001/snapshots/compare?snapshot_a=999&snapshot_b=2")
    assert r3.status_code == 404, r3.text

    # Edge: snapshot belongs to different perspective
    s2 = TS()
    s2.add(Perspective(id="other", name="Other", description="", facet_filters={}, created_by="t"))
    s2.add(PerspectiveSnapshot(id=3, perspective_id="other",
                               membership=[{"server_id": "x", "risk_tier": "LOW"}]))
    s2.commit(); s2.close()
    r4 = c.get("/api/perspectives/perm_001/snapshots/compare?snapshot_a=3&snapshot_b=2")
    assert r4.status_code == 404, r4.text  # snapshot 3 belongs to 'other', not 'perm_001'

    print("PASS")
