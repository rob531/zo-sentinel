# deps: fastapi, sqlalchemy, pydantic
"""Perspective Snapshot History Service.

Returns snapshot history for a perspective and per-snapshot membership detail
from the app Postgres via the standard get_session dependency.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, desc, func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

_repo_root = Path(__file__).resolve().parents[4]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import Base, McpServerRegistry, Perspective, PerspectiveSnapshot

router = APIRouter(prefix="/api", tags=["perspective_snapshot_history"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class SnapshotSummary(BaseModel):
    id: int
    perspective_id: str
    taken_at: datetime
    member_count: int

    model_config = ConfigDict(from_attributes=True)


class SnapshotListResponse(BaseModel):
    perspective_id: str
    perspective_name: str
    snapshots: list[SnapshotSummary]
    total: int
    limit: int
    offset: int


class MemberEntry(BaseModel):
    server_id: str
    tier: Optional[str] = None
    hostname: Optional[str] = None
    meta: Optional[dict[str, Any]] = None


class SnapshotDetailResponse(BaseModel):
    id: int
    perspective_id: str
    perspective_name: str
    taken_at: datetime
    member_count: int
    members: list[MemberEntry]


class SnapshotDiffItem(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    change_type: str  # "added" | "removed" | "tier_changed"


class SnapshotCompareResponse(BaseModel):
    perspective_id: str
    older_snapshot_id: int
    newer_snapshot_id: int
    older_taken_at: datetime
    newer_taken_at: datetime
    added_count: int
    removed_count: int
    tier_changed_count: int
    diff: list[SnapshotDiffItem]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_membership(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return []
    if isinstance(raw, dict):
        # legacy single-level dict: treat as one-entry list
        return [raw]
    return []


def _server_names(session: Session, server_ids: list[str]) -> dict[str, str]:
    if not server_ids:
        return {}
    rows = (
        session.query(McpServerRegistry.server_id, McpServerRegistry.name)
        .filter(McpServerRegistry.server_id.in_(server_ids))
        .all()
    )
    return {r.server_id: r.name for r in rows}


def _tier_of(entry: dict[str, Any]) -> Optional[str]:
    return entry.get("tier") or entry.get("risk_tier") or (entry.get("meta") or {}).get("tier")


def _build_diff(
    older_membership: list[dict[str, Any]],
    newer_membership: list[dict[str, Any]],
    names: dict[str, str],
) -> tuple[list[SnapshotDiffItem], int, int, int]:
    older_map: dict[str, dict[str, Any]] = {
        e["server_id"]: e for e in older_membership if "server_id" in e
    }
    newer_map: dict[str, dict[str, Any]] = {
        e["server_id"]: e for e in newer_membership if "server_id" in e
    }

    added: set[str] = set(newer_map) - set(older_map)
    removed: set[str] = set(older_map) - set(newer_map)
    common: set[str] = set(newer_map) & set(older_map)

    diff: list[SnapshotDiffItem] = []

    for sid in sorted(added):
        diff.append(SnapshotDiffItem(
            server_id=sid,
            server_name=names.get(sid),
            old_tier=None,
            new_tier=_tier_of(newer_map[sid]),
            change_type="added",
        ))

    for sid in sorted(removed):
        diff.append(SnapshotDiffItem(
            server_id=sid,
            server_name=names.get(sid),
            old_tier=_tier_of(older_map[sid]),
            new_tier=None,
            change_type="removed",
        ))

    tier_changed = 0
    for sid in sorted(common):
        old_t = _tier_of(older_map[sid])
        new_t = _tier_of(newer_map[sid])
        if old_t != new_t:
            tier_changed += 1
            diff.append(SnapshotDiffItem(
                server_id=sid,
                server_name=names.get(sid),
                old_tier=old_t,
                new_tier=new_t,
                change_type="tier_changed",
            ))

    return diff, len(added), len(removed), tier_changed


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/perspectives/{perspective_id}/snapshots",
    response_model=SnapshotListResponse,
)
def list_snapshot_history(
    perspective_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    date_from: Optional[str] = Query(default=None, description="ISO date string, e.g. 2024-01-01"),
    date_to: Optional[str] = Query(default=None, description="ISO date string, e.g. 2024-12-31"),
    db: Session = Depends(get_session),
) -> SnapshotListResponse:
    """Return paginated snapshot history for a perspective, newest first."""
    perspective = (
        db.query(Perspective)
        .filter(Perspective.id == perspective_id)
        .first()
    )
    if not perspective:
        raise HTTPException(status_code=404, detail=f"Perspective {perspective_id} not found")

    total = (
        db.query(func.count())
        .select_from(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .scalar()
    ) or 0

    q = (
        db.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(desc(PerspectiveSnapshot.taken_at))
        .offset(offset)
        .limit(limit)
    )

    rows = q.all()

    snapshots = []
    for r in rows:
        membership = _parse_membership(r.membership)
        snapshots.append(SnapshotSummary(
            id=r.id,
            perspective_id=r.perspective_id,
            taken_at=r.taken_at,
            member_count=len(membership),
        ))

    return SnapshotListResponse(
        perspective_id=perspective_id,
        perspective_name=perspective.name,
        snapshots=snapshots,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/perspectives/{perspective_id}/snapshots/{snapshot_id}",
    response_model=SnapshotDetailResponse,
)
def get_snapshot_detail(
    perspective_id: str,
    snapshot_id: int,
    db: Session = Depends(get_session),
) -> SnapshotDetailResponse:
    """Return a single snapshot with full membership."""
    snapshot = (
        db.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.id == snapshot_id)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .first()
    )
    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot {snapshot_id} not found for perspective {perspective_id}",
        )

    perspective = (
        db.query(Perspective)
        .filter(Perspective.id == perspective_id)
        .first()
    )
    perspective_name = perspective.name if perspective else ""

    membership = _parse_membership(snapshot.membership)
    members = [
        MemberEntry(
            server_id=e.get("server_id", ""),
            tier=_tier_of(e),
            hostname=e.get("hostname") or e.get("name"),
            meta=e.get("meta"),
        )
        for e in membership
        if e.get("server_id")
    ]

    return SnapshotDetailResponse(
        id=snapshot.id,
        perspective_id=snapshot.perspective_id,
        perspective_name=perspective_name,
        taken_at=snapshot.taken_at,
        member_count=len(members),
        members=members,
    )


@router.get(
    "/perspectives/{perspective_id}/snapshots/compare",
    response_model=SnapshotCompareResponse,
)
def compare_snapshots(
    perspective_id: str,
    older_id: int = Query(..., description="Older snapshot ID"),
    newer_id: int = Query(..., description="Newer snapshot ID"),
    db: Session = Depends(get_session),
) -> SnapshotCompareResponse:
    """Compare membership between two snapshots of the same perspective."""
    older = (
        db.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.id == older_id)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .first()
    )
    newer = (
        db.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.id == newer_id)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .first()
    )
    if not older:
        raise HTTPException(status_code=404, detail=f"Older snapshot {older_id} not found")
    if not newer:
        raise HTTPException(status_code=404, detail=f"Newer snapshot {newer_id} not found")

    older_mem = _parse_membership(older.membership)
    newer_mem = _parse_membership(newer.membership)

    all_ids = {e["server_id"] for e in older_mem if "server_id" in e} | {
        e["server_id"] for e in newer_mem if "server_id" in e
    }
    names = _server_names(db, list(all_ids))

    diff, added, removed, tier_changed = _build_diff(older_mem, newer_mem, names)

    return SnapshotCompareResponse(
        perspective_id=perspective_id,
        older_snapshot_id=older_id,
        newer_snapshot_id=newer_id,
        older_taken_at=older.taken_at,
        newer_taken_at=newer.taken_at,
        added_count=added,
        removed_count=removed,
        tier_changed_count=tier_changed,
        diff=diff,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from datetime import datetime, timezone

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    now = datetime.now(timezone.utc)

    with TestSessionLocal() as sess:
        sess.add(Perspective(id="persp-abc", org_id="100", name="Production Servers", facet_filters={}, created_by="admin"))
        sess.add(Perspective(id="persp-xyz", org_id="100", name="Dev Servers", facet_filters={}, created_by="admin"))
        sess.add(McpServerRegistry(server_id="srv-001", name="Web Server 1", risk_tier="HIGH", verdict="clean", confidence=0.9))
        sess.add(McpServerRegistry(server_id="srv-002", name="DB Server 1", risk_tier="LOW", verdict="clean", confidence=0.8))
        sess.add(McpServerRegistry(server_id="srv-003", name="Cache Server 1", risk_tier="MEDIUM", verdict="clean", confidence=0.7))
        snap1 = PerspectiveSnapshot(
            id=1,
            perspective_id="persp-abc",
            taken_at=now,
            membership=[
                {"server_id": "srv-001", "tier": "HIGH", "hostname": "web-1"},
                {"server_id": "srv-002", "tier": "LOW", "hostname": "db-1"},
                {"server_id": "srv-003", "tier": "MEDIUM", "hostname": "cache-1"},
            ],
        )
        snap2 = PerspectiveSnapshot(
            id=2,
            perspective_id="persp-abc",
            taken_at=now,
            membership=[
                {"server_id": "srv-001", "tier": "HIGH", "hostname": "web-1"},
                {"server_id": "srv-003", "tier": "HIGH", "hostname": "cache-1"},
            ],
        )
        snap3 = PerspectiveSnapshot(
            id=3,
            perspective_id="persp-xyz",
            taken_at=now,
            membership=[{"server_id": "srv-001", "tier": "LOW"}],
        )
        sess.add_all([snap1, snap2, snap3])
        sess.commit()

    client = TestClient(app)

    # Test 1: list snapshots for persp-abc
    resp = client.get("/api/perspectives/persp-abc/snapshots?limit=10&offset=0")
    assert resp.status_code == 200, f"FAIL: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["perspective_id"] == "persp-abc"
    assert data["perspective_name"] == "Production Servers"
    assert len(data["snapshots"]) == 2
    assert data["total"] == 2
    assert data["snapshots"][0]["member_count"] == 2
    assert data["snapshots"][1]["member_count"] == 3

    # Test 2: snapshot detail
    resp2 = client.get("/api/perspectives/persp-abc/snapshots/1")
    assert resp2.status_code == 200, f"FAIL: {resp2.status_code} {resp2.text}"
    detail = resp2.json()
    assert detail["id"] == 1
    assert detail["perspective_name"] == "Production Servers"
    assert detail["member_count"] == 3
    assert len(detail["members"]) == 3
    tier_map = {m["server_id"]: m["tier"] for m in detail["members"]}
    assert tier_map.get("srv-001") == "HIGH"
    assert tier_map.get("srv-002") == "LOW"

    # Test 3: compare snapshots
    resp3 = client.get("/api/perspectives/persp-abc/snapshots/compare", params={"older_id": 1, "newer_id": 2})
    assert resp3.status_code == 200, f"FAIL: {resp3.status_code} {resp3.text}"
    cmp_data = resp3.json()
    assert cmp_data["older_snapshot_id"] == 1
    assert cmp_data["newer_snapshot_id"] == 2
    assert cmp_data["removed_count"] == 1
    assert cmp_data["added_count"] == 0
    assert cmp_data["tier_changed_count"] == 1

    removed = [d for d in cmp_data["diff"] if d["change_type"] == "removed"]
    assert len(removed) == 1
    assert removed[0]["server_id"] == "srv-002"
    changed = [d for d in cmp_data["diff"] if d["change_type"] == "tier_changed"]
    assert len(changed) == 1
    assert changed[0]["server_id"] == "srv-003"
    assert changed[0]["old_tier"] == "MEDIUM"
    assert changed[0]["new_tier"] == "HIGH"
    assert changed[0]["server_name"] == "Cache Server 1"

    # Test 4: 404 for unknown perspective
    resp4 = client.get("/api/perspectives/nonexistent/snapshots")
    assert resp4.status_code == 404

    # Test 5: 404 for unknown snapshot
    resp5 = client.get("/api/perspectives/persp-abc/snapshots/999")
    assert resp5.status_code == 404

    # Test 6: 404 for snapshot of wrong perspective
    resp6 = client.get("/api/perspectives/persp-abc/snapshots/3")
    assert resp6.status_code == 404

    # Test 7: pagination
    resp7 = client.get("/api/perspectives/persp-abc/snapshots?limit=1&offset=0")
    assert resp7.status_code == 200
    assert len(resp7.json()["snapshots"]) == 1
    assert resp7.json()["total"] == 2

    # Test 8: empty perspective (no snapshots)
    resp8 = client.get("/api/perspectives/persp-xyz/snapshots")
    assert resp8.status_code == 200
    assert len(resp8.json()["snapshots"]) == 1  # has snap3

    print("PASS")
