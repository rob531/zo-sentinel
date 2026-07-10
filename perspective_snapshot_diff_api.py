"""perspective_snapshot_diff_api.py -- Diff between two perspective snapshots.

Compares membership JSON arrays from two snapshots and returns added/removed/unchanged server_ids.
"""
from __future__ import annotations

from typing import Optional, Set

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveSnapshot, Perspective

router = APIRouter(prefix="/api/perspectives", tags=["perspectives"])


class DiffRequest(BaseModel):
    perspective_id: str
    snapshot_a_id: int
    snapshot_b_id: int


class DiffResponse(BaseModel):
    perspective_id: str
    snapshot_a_id: int
    snapshot_b_id: int
    added: list[str]
    removed: list[str]
    unchanged: list[str]
    delta_count: int


def _get_servers(membership: Optional[dict]) -> Set[str]:
    """Extract server_id set from membership JSON dict {server_id: risk_tier}."""
    return set(membership.keys()) if membership else set()


@router.post("/diff", response_model=DiffResponse)
def diff_snapshots(payload: DiffRequest,
                   db: Session = Depends(get_session)) -> DiffResponse:
    """Compute diff between two snapshots of a perspective.

    membership is stored as {server_id: risk_tier}, so we compare the keys only.
    """
    # Verify perspective exists
    persp = db.execute(
        select(Perspective).where(Perspective.id == payload.perspective_id)
    ).scalar_one_or_none()
    if persp is None:
        raise HTTPException(status_code=404,
                            detail=f"Perspective {payload.perspective_id!r} not found")

    # Load both snapshots
    snap_a = db.execute(
        select(PerspectiveSnapshot).where(
            PerspectiveSnapshot.id == payload.snapshot_a_id,
            PerspectiveSnapshot.perspective_id == payload.perspective_id
        )
    ).scalar_one_or_none()
    if snap_a is None:
        raise HTTPException(status_code=404,
                            detail=f"Snapshot {payload.snapshot_a_id} not found for perspective {payload.perspective_id!r}")

    snap_b = db.execute(
        select(PerspectiveSnapshot).where(
            PerspectiveSnapshot.id == payload.snapshot_b_id,
            PerspectiveSnapshot.perspective_id == payload.perspective_id
        )
    ).scalar_one_or_none()
    if snap_b is None:
        raise HTTPException(status_code=404,
                            detail=f"Snapshot {payload.snapshot_b_id} not found for perspective {payload.perspective_id!r}")

    servers_a = _get_servers(snap_a.membership)
    servers_b = _get_servers(snap_b.membership)

    added = sorted(servers_b - servers_a)
    removed = sorted(servers_a - servers_b)
    unchanged = sorted(servers_a & servers_b)

    return DiffResponse(
        perspective_id=payload.perspective_id,
        snapshot_a_id=payload.snapshot_a_id,
        snapshot_b_id=payload.snapshot_b_id,
        added=added,
        removed=removed,
        unchanged=unchanged,
        delta_count=len(added) + len(removed),
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
    # Seed perspective
    s.add(Perspective(id="persp1", name="Test Perspective",
                      description="", facet_filters={}, created_by="test"))
    # Snapshot A: servers s1, s2, s3 (shared), s4 (removed), s5 (removed)
    s.add(PerspectiveSnapshot(id=1, perspective_id="persp1",
                              membership={"s1": "HIGH", "s2": "MEDIUM", "s3": "LOW",
                                          "s4": "HIGH", "s5": "CRITICAL"}))
    # Snapshot B: servers s1, s2, s3 (shared), s6 (added), s7 (added)
    s.add(PerspectiveSnapshot(id=2, perspective_id="persp1",
                              membership={"s1": "HIGH", "s2": "MEDIUM", "s3": "LOW",
                                          "s6": "MEDIUM", "s7": "LOW"}))
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

    # Happy path: diff A vs B
    r = c.post("/api/perspectives/diff",
               json={"perspective_id": "persp1", "snapshot_a_id": 1, "snapshot_b_id": 2})
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j["added"]) == {"s6", "s7"}, f"added={j['added']}"
    assert set(j["removed"]) == {"s4", "s5"}, f"removed={j['removed']}"
    assert set(j["unchanged"]) == {"s1", "s2", "s3"}, f"unchanged={j['unchanged']}"
    assert j["delta_count"] == 4, j
    assert j["perspective_id"] == "persp1"

    # Edge: reversed diff (B vs A)
    r2 = c.post("/api/perspectives/diff",
                json={"perspective_id": "persp1", "snapshot_a_id": 2, "snapshot_b_id": 1})
    j2 = r2.json()
    assert set(j2["added"]) == {"s4", "s5"}, f"reversed added={j2['added']}"
    assert set(j2["removed"]) == {"s6", "s7"}, f"reversed removed={j2['removed']}"

    # Edge: missing perspective
    r3 = c.post("/api/perspectives/diff",
                json={"perspective_id": "nope", "snapshot_a_id": 1, "snapshot_b_id": 2})
    assert r3.status_code == 404

    # Edge: missing snapshot
    r4 = c.post("/api/perspectives/diff",
                json={"perspective_id": "persp1", "snapshot_a_id": 999, "snapshot_b_id": 2})
    assert r4.status_code == 404

    print("PASS")