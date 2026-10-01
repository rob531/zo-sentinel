# deps: fastapi, sqlalchemy, pydantic
"""Perspective Snapshot Query API

Public endpoints for querying perspective snapshots and comparing membership
across snapshots. Reads from perspective_snapshots and perspective_events
via the standard app DB session dependency.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveSnapshot, PerspectiveEvent, McpServerRegistry

router = APIRouter(prefix="/api/perspectives", tags=["perspective_snapshot_query_api"])


# --- Request / Response models ---------------------------------------------

class SnapshotSummary(BaseModel):
    id: int
    perspective_id: str
    taken_at: datetime
    member_count: int

    class Config:
        from_attributes = True


class SnapshotListResponse(BaseModel):
    perspective_id: str
    snapshots: list[SnapshotSummary]
    total: int
    limit: int
    offset: int


class SnapshotMember(BaseModel):
    server_id: str
    server_name: Optional[str]
    tier: str


class SnapshotDetailResponse(BaseModel):
    id: int
    perspective_id: str
    taken_at: datetime
    membership: dict
    member_count: int
    members: list[SnapshotMember]


class SnapshotDiff(BaseModel):
    server_id: str
    server_name: Optional[str]
    old_tier: Optional[str]
    new_tier: Optional[str]
    change_type: str  # "added" | "removed" | "tier_changed"


class SnapshotCompareResponse(BaseModel):
    perspective_id: str
    older_snapshot_id: int
    newer_snapshot_id: int
    older_taken_at: datetime
    newer_taken_at: datetime
    diff: list[SnapshotDiff]
    added_count: int
    removed_count: int
    tier_changed_count: int


# --- Helpers ---------------------------------------------------------------

def _get_server_names(session: Session, server_ids: list[str]) -> dict[str, str]:
    """Return {server_id: name} for the given server IDs."""
    if not server_ids:
        return {}
    rows = (
        session.query(McpServerRegistry.server_id, McpServerRegistry.name)
        .filter(McpServerRegistry.server_id.in_(server_ids))
        .all()
    )
    return {r.server_id: r.name for r in rows}


def _build_diff(older: dict, newer: dict, names: dict[str, str]) -> list[SnapshotDiff]:
    diffs = []
    older_keys = set(older.keys())
    newer_keys = set(newer.keys())

    for sid in sorted(newer_keys - older_keys):
        diffs.append(SnapshotDiff(
            server_id=sid,
            server_name=names.get(sid),
            old_tier=None,
            new_tier=newer[sid],
            change_type="added",
        ))
    for sid in sorted(older_keys - newer_keys):
        diffs.append(SnapshotDiff(
            server_id=sid,
            server_name=names.get(sid),
            old_tier=older[sid],
            new_tier=None,
            change_type="removed",
        ))
    for sid in sorted(older_keys & newer_keys):
        if older[sid] != newer[sid]:
            diffs.append(SnapshotDiff(
                server_id=sid,
                server_name=names.get(sid),
                old_tier=older[sid],
                new_tier=newer[sid],
                change_type="tier_changed",
            ))
    return diffs


# --- Endpoints ------------------------------------------------------------

@router.get(
    "/{perspective_id}/snapshots",
    response_model=SnapshotListResponse,
)
def list_snapshots(
    perspective_id: str,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> SnapshotListResponse:
    """Return paginated snapshot history for a perspective, newest first."""
    total = (
        db.query(func.count(PerspectiveSnapshot.id))
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .scalar()
    )

    rows = (
        db.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(desc(PerspectiveSnapshot.taken_at))
        .offset(offset)
        .limit(limit)
        .all()
    )

    snapshots = [
        SnapshotSummary(
            id=r.id,
            perspective_id=r.perspective_id,
            taken_at=r.taken_at,
            member_count=len(r.membership) if r.membership else 0,
        )
        for r in rows
    ]

    return SnapshotListResponse(
        perspective_id=perspective_id,
        snapshots=snapshots,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{perspective_id}/snapshots/{snapshot_id}",
    response_model=SnapshotDetailResponse,
)
def get_snapshot(
    perspective_id: str,
    snapshot_id: int,
    db: Session = Depends(get_session),
) -> SnapshotDetailResponse:
    """Return a specific snapshot with server names resolved."""
    snap = (
        db.query(PerspectiveSnapshot)
        .filter(
            PerspectiveSnapshot.id == snapshot_id,
            PerspectiveSnapshot.perspective_id == perspective_id,
        )
        .first()
    )
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    membership = snap.membership or {}
    server_ids = list(membership.keys())
    names = _get_server_names(db, server_ids)

    members = [
        SnapshotMember(server_id=sid, server_name=names.get(sid), tier=tier)
        for sid, tier in membership.items()
    ]

    return SnapshotDetailResponse(
        id=snap.id,
        perspective_id=snap.perspective_id,
        taken_at=snap.taken_at,
        membership=membership,
        member_count=len(membership),
        members=members,
    )


@router.get(
    "/{perspective_id}/snapshots/{snapshot_id}/events",
    response_model=list,
)
def snapshot_events(
    perspective_id: str,
    snapshot_id: int,
    db: Session = Depends(get_session),
) -> list:
    """Return perspective events that occurred up to (and including) the snapshot's taken_at time."""
    snap = (
        db.query(PerspectiveSnapshot)
        .filter(
            PerspectiveSnapshot.id == snapshot_id,
            PerspectiveSnapshot.perspective_id == perspective_id,
        )
        .first()
    )
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    rows = (
        db.query(PerspectiveEvent)
        .filter(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.created_at <= snap.taken_at,
        )
        .order_by(desc(PerspectiveEvent.created_at))
        .limit(100)
        .all()
    )
    return [
        {
            "id": r.id,
            "server_id": r.server_id,
            "change_type": r.change_type,
            "old_tier": r.old_tier,
            "new_tier": r.new_tier,
            "seen": r.seen,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.get(
    "/{perspective_id}/snapshots/compare",
    response_model=SnapshotCompareResponse,
)
def compare_snapshots(
    perspective_id: str,
    older_snapshot_id: int = Query(..., ge=1),
    newer_snapshot_id: int = Query(..., ge=1),
    db: Session = Depends(get_session),
) -> SnapshotCompareResponse:
    """Return the membership diff between two snapshots of the same perspective.

    The perspective_id in the path must match both snapshots.
    """
    if older_snapshot_id == newer_snapshot_id:
        raise HTTPException(
            status_code=400,
            detail="Provide two different snapshot IDs to compare.",
        )

    older = (
        db.query(PerspectiveSnapshot)
        .filter(
            PerspectiveSnapshot.id == older_snapshot_id,
            PerspectiveSnapshot.perspective_id == perspective_id,
        )
        .first()
    )
    newer = (
        db.query(PerspectiveSnapshot)
        .filter(
            PerspectiveSnapshot.id == newer_snapshot_id,
            PerspectiveSnapshot.perspective_id == perspective_id,
        )
        .first()
    )

    if not older:
        raise HTTPException(status_code=404, detail=f"Snapshot {older_snapshot_id} not found")
    if not newer:
        raise HTTPException(status_code=404, detail=f"Snapshot {newer_snapshot_id} not found")

    older_mem = older.membership or {}
    newer_mem = newer.membership or {}

    all_ids = set(older_mem.keys()) | set(newer_mem.keys())
    names = _get_server_names(db, list(all_ids))

    diffs = _build_diff(older_mem, newer_mem, names)

    added = sum(1 for d in diffs if d.change_type == "added")
    removed = sum(1 for d in diffs if d.change_type == "removed")
    changed = sum(1 for d in diffs if d.change_type == "tier_changed")

    return SnapshotCompareResponse(
        perspective_id=perspective_id,
        older_snapshot_id=older_snapshot_id,
        newer_snapshot_id=newer_snapshot_id,
        older_taken_at=older.taken_at,
        newer_taken_at=newer.taken_at,
        diff=diffs,
        added_count=added,
        removed_count=removed,
        tier_changed_count=changed,
    )


# --- Self-test ------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from contextlib import contextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base

    sqlite_engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=sqlite_engine)
    TestSession = sessionmaker(bind=sqlite_engine, expire_on_commit=False)

    @contextmanager
    def get_test_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    with TestSession() as s:
        s.add_all([
            McpServerRegistry(
                server_id="srv-001",
                name="Server Alpha",
                registry_source="test",
                url="https://example.com",
            ),
            McpServerRegistry(
                server_id="srv-002",
                name="Server Beta",
                registry_source="test",
                url="https://example2.com",
            ),
            McpServerRegistry(
                server_id="srv-003",
                name="Server Gamma",
                registry_source="test",
                url="https://example3.com",
            ),
            PerspectiveSnapshot(
                perspective_id="pid-test",
                taken_at=datetime(2024, 1, 1, 10, 0, 0),
                membership={"srv-001": "low", "srv-002": "medium"},
            ),
            PerspectiveSnapshot(
                perspective_id="pid-test",
                taken_at=datetime(2024, 2, 1, 10, 0, 0),
                membership={"srv-001": "high", "srv-002": "medium", "srv-003": "low"},
            ),
            PerspectiveEvent(
                perspective_id="pid-test",
                server_id="srv-001",
                change_type="tier_changed",
                old_tier="low",
                new_tier="high",
                seen=True,
                created_at=datetime(2024, 2, 1, 9, 0, 0),
            ),
            PerspectiveEvent(
                perspective_id="pid-test",
                server_id="srv-003",
                change_type="entered",
                old_tier=None,
                new_tier="low",
                seen=False,
                created_at=datetime(2024, 2, 1, 10, 30, 0),
            ),
        ])
        s.commit()

    client = TestClient(app)

    # Test 1: list snapshots
    resp = client.get("/api/perspectives/pid-test/snapshots?limit=10&offset=0")
    if resp.status_code != 200:
        print(f"FAIL: list snapshots returned {resp.status_code}")
        sys.exit(1)
    data = resp.json()
    if data["total"] != 2:
        print(f"FAIL: expected 2 snapshots, got {data['total']}")
        sys.exit(1)
    if len(data["snapshots"]) != 2:
        print(f"FAIL: expected 2 snapshot summaries, got {len(data['snapshots'])}")
        sys.exit(1)

    # Test 2: get snapshot detail with server names
    resp2 = client.get("/api/perspectives/pid-test/snapshots/1")
    if resp2.status_code != 200:
        print(f"FAIL: get snapshot returned {resp2.status_code}")
        sys.exit(1)
    detail = resp2.json()
    if detail["member_count"] != 2:
        print(f"FAIL: expected 2 members, got {detail['member_count']}")
        sys.exit(1)
    # srv-001 should have name "Server Alpha"
    member_names = {m["server_id"]: m["server_name"] for m in detail["members"]}
    if member_names.get("srv-001") != "Server Alpha":
        print(f"FAIL: srv-001 name should be 'Server Alpha', got {member_names.get('srv-001')}")
        sys.exit(1)

    # Test 3: snapshot events
    resp3 = client.get("/api/perspectives/pid-test/snapshots/1/events")
    if resp3.status_code != 200:
        print(f"FAIL: snapshot events returned {resp3.status_code}")
        sys.exit(1)
    events = resp3.json()
    if len(events) != 2:
        print(f"FAIL: expected 2 events, got {len(events)}")
        sys.exit(1)

    # Test 4: compare snapshots
    resp4 = client.get(
        "/api/perspectives/pid-test/snapshots/compare"
        "?older_snapshot_id=1&newer_snapshot_id=2"
    )
    if resp4.status_code != 200:
        print(f"FAIL: compare returned {resp4.status_code} -- {resp4.text}")
        sys.exit(1)
    cmp_data = resp4.json()
    if cmp_data["added_count"] != 1:
        print(f"FAIL: expected 1 added, got {cmp_data['added_count']}")
        sys.exit(1)
    if cmp_data["tier_changed_count"] != 1:
        print(f"FAIL: expected 1 tier_changed, got {cmp_data['tier_changed_count']}")
        sys.exit(1)
    if cmp_data["removed_count"] != 0:
        print(f"FAIL: expected 0 removed, got {cmp_data['removed_count']}")
        sys.exit(1)

    # Test 5: compare same snapshot -> 400
    resp5 = client.get(
        "/api/perspectives/pid-test/snapshots/compare"
        "?older_snapshot_id=1&newer_snapshot_id=1"
    )
    if resp5.status_code != 400:
        print(f"FAIL: same-id compare should be 400, got {resp5.status_code}")
        sys.exit(1)

    # Test 6: nonexistent snapshot -> 404
    resp6 = client.get("/api/perspectives/pid-test/snapshots/99999")
    if resp6.status_code != 404:
        print(f"FAIL: nonexistent snapshot should be 404, got {resp6.status_code}")
        sys.exit(1)

    print("PASS")
