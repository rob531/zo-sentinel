# deps: fastapi, sqlalchemy, pydantic
"""Perspective Diff Service -- compare a snapshot against its predecessor."""

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot, PerspectiveEvent

router = APIRouter(prefix="/api", tags=["perspectives"])


class ServerChange(BaseModel):
    server_id: str
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None


class ChangeSet(BaseModel):
    added: List[ServerChange] = []
    removed: List[ServerChange] = []
    tier_changed: List[ServerChange] = []


class DiffResponse(BaseModel):
    perspective_id: str
    snapshot_id: int
    previous_snapshot_id: Optional[int] = None
    changes: ChangeSet


class SnapshotHeader(BaseModel):
    id: int
    perspective_id: str
    taken_at: datetime
    member_count: int


class SnapshotListResponse(BaseModel):
    perspective_id: str
    snapshots: List[SnapshotHeader]


# -------------------------------------------------------------------------- #
# Endpoints
# -------------------------------------------------------------------------- #

@router.get(
    "/perspectives/{perspective_id}/diff",
    response_model=DiffResponse,
    name="perspective_diff:diff",
)
def diff_snapshot(
    perspective_id: str,
    snapshot_id: int,
    db: Session = Depends(get_session),
) -> DiffResponse:
    """Compare a given snapshot against the most recent prior snapshot for the
    same perspective.  Returns the sets of servers that entered, left, or
    changed risk tier between the two snapshots."""
    # Resolve perspective
    perspective = db.query(Perspective).filter(
        Perspective.id == perspective_id
    ).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")

    # Load all snapshots for this perspective ordered by taken_at
    snapshots = (
        db.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(asc(PerspectiveSnapshot.taken_at))
        .all()
    )

    if not snapshots:
        raise HTTPException(status_code=404, detail="No snapshots found for perspective")

    # Locate target snapshot
    target: Optional[PerspectiveSnapshot] = None
    for s in snapshots:
        if s.id == snapshot_id:
            target = s
            break
    if target is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    idx = snapshots.index(target)
    previous_snapshot_id: Optional[int] = None
    prev_membership: Dict[str, str] = {}

    if idx > 0:
        prev = snapshots[idx - 1]
        previous_snapshot_id = prev.id
        prev_membership = (prev.membership or {})

    curr_membership: Dict[str, str] = (target.membership or {})
    prev_keys = set(prev_membership.keys())
    curr_keys = set(curr_membership.keys())

    added_keys = sorted(curr_keys - prev_keys)
    removed_keys = sorted(prev_keys - curr_keys)
    shared_keys = sorted(curr_keys & prev_keys)
    tier_changed_keys = sorted(k for k in shared_keys if prev_membership[k] != curr_membership[k])

    changes = ChangeSet(
        added=[ServerChange(server_id=k, new_tier=curr_membership[k]) for k in added_keys],
        removed=[ServerChange(server_id=k, old_tier=prev_membership[k]) for k in removed_keys],
        tier_changed=[
            ServerChange(
                server_id=k,
                old_tier=prev_membership[k],
                new_tier=curr_membership[k],
            )
            for k in tier_changed_keys
        ],
    )

    return DiffResponse(
        perspective_id=perspective_id,
        snapshot_id=snapshot_id,
        previous_snapshot_id=previous_snapshot_id,
        changes=changes,
    )


@router.get(
    "/perspectives/{perspective_id}/snapshots",
    response_model=SnapshotListResponse,
    name="perspective_diff:snapshots",
)
def list_snapshots(
    perspective_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_session),
) -> SnapshotListResponse:
    """Return a paginated list of snapshot headers for a perspective."""
    rows = (
        db.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(desc(PerspectiveSnapshot.taken_at))
        .offset(skip)
        .limit(limit)
        .all()
    )

    snapshots = [
        SnapshotHeader(
            id=s.id,
            perspective_id=s.perspective_id,
            taken_at=s.taken_at,
            member_count=len(s.membership) if s.membership else 0,
        )
        for s in rows
    ]
    return SnapshotListResponse(perspective_id=perspective_id, snapshots=snapshots)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    from app.models import Base

    _eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=_eng)
    _TS = sessionmaker(bind=_eng, autoflush=False, autocommit=False)

    with _TS() as db:
        db.execute(text(
            "INSERT INTO perspectives (id, name, description, facet_filters, created_by) "
            "VALUES ('p1','Test Perspective','',{},'test-user')"
        ).format("{}"))
        db.execute(text(
            "INSERT INTO perspective_snapshots (id, perspective_id, taken_at, membership) "
            "VALUES "
            "(1,'p1','2023-01-01T00:00:00','{}'),"
            "(2,'p1','2023-01-02T00:00:00','{\"srv-a\":\"low\",\"srv-b\":\"medium\"}'),"
            "(3,'p1','2023-01-03T00:00:00','{\"srv-a\":\"high\",\"srv-c\":\"low\"}')"
        ))
        db.commit()

    _that_app = FastAPI()
    _that_app.include_router(router)

    def _override_session():
        sess = _TS()
        try:
            yield sess
        finally:
            sess.close()

    _that_app.dependency_overrides[get_session] = _override_session
    c = TestClient(_that_app)

    # Snapshot 1 vs 2: srv-a, srv-b added (no previous snapshot)
    resp = c.get("/api/perspectives/p1/diff", params={"snapshot_id": 2})
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["previous_snapshot_id"] == 1
    added_ids = [x["server_id"] for x in data["changes"]["added"]]
    assert "srv-a" in added_ids
    assert "srv-b" in added_ids

    # Snapshot 2 vs 3: srv-a tier changed (medium->high), srv-b removed, srv-c added
    resp = c.get("/api/perspectives/p1/diff", params={"snapshot_id": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert data["previous_snapshot_id"] == 2
    assert data["changes"]["removed"][0]["server_id"] == "srv-b"
    tier_change = next(x for x in data["changes"]["tier_changed"] if x["server_id"] == "srv-a")
    assert tier_change["old_tier"] == "medium"
    assert tier_change["new_tier"] == "high"
    added_ids = [x["server_id"] for x in data["changes"]["added"]]
    assert "srv-c" in added_ids

    # List snapshots
    resp = c.get("/api/perspectives/p1/snapshots")
    assert resp.status_code == 200
    snaps = resp.json()["snapshots"]
    assert len(snaps) == 3
    assert snaps[0]["id"] == 3  # newest first

    # 404 on missing perspective
    resp = c.get("/api/perspectives/none/diff", params={"snapshot_id": 1})
    assert resp.status_code == 404

    # 404 on missing snapshot
    resp = c.get("/api/perspectives/p1/diff", params={"snapshot_id": 999})
    assert resp.status_code == 404

    print("PASS")
