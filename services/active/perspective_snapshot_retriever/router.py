# deps: fastapi, sqlalchemy, pydantic
"""Perspective Snapshot Retriever.

Retrieves perspective snapshots and membership detail from the app Postgres
via the standard get_session dependency.

prefix="/api", tag="perspective_snapshot_retriever"
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
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
from app.models import (
    Base,
    McpServerRegistry,
    Perspective,
    PerspectiveEvent,
    PerspectiveSnapshot,
)

router = APIRouter(prefix="/api", tags=["perspective_snapshot_retriever"])


# ---------------------------------------------------------------------------
# Pydantic models
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


class SnapshotMember(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    tier: Optional[str] = None


class SnapshotDetailResponse(BaseModel):
    id: int
    perspective_id: str
    perspective_name: str
    taken_at: datetime
    member_count: int
    members: list[SnapshotMember]


class SnapshotEvent(BaseModel):
    id: int
    server_id: str
    change_type: str
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    seen: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SnapshotEventsResponse(BaseModel):
    perspective_id: str
    snapshot_id: int
    snapshot_taken_at: datetime
    events: list[SnapshotEvent]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_membership(raw: Any) -> dict[str, str]:
    """Normalise membership to {server_id: tier} regardless of stored format."""
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except Exception:
            pass
    if isinstance(raw, list):
        result: dict[str, str] = {}
        for item in raw:
            if isinstance(item, dict):
                sid = item.get("server_id")
                tier = item.get("tier") or item.get("risk_tier")
                if sid and tier:
                    result[str(sid)] = str(tier)
        return result
    return {}


def _server_names(session: Session, server_ids: list[str]) -> dict[str, str]:
    if not server_ids:
        return {}
    rows = (
        session.query(McpServerRegistry.server_id, McpServerRegistry.name)
        .filter(McpServerRegistry.server_id.in_(server_ids))
        .all()
    )
    return {r.server_id: r.name for r in rows}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/perspectives/{perspective_id}/snapshots",
    response_model=SnapshotListResponse,
)
def list_snapshots(
    perspective_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
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

    q = (
        db.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(desc(PerspectiveSnapshot.taken_at))
    )

    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from)
            q = q.filter(PerspectiveSnapshot.taken_at >= dt_from)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date_from format")
    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to)
            q = q.filter(PerspectiveSnapshot.taken_at <= dt_to)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date_to format")

    total = (
        db.query(func.count(PerspectiveSnapshot.id))
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .scalar()
    ) or 0

    rows = q.offset(offset).limit(limit).all()

    snapshots = [
        SnapshotSummary(
            id=r.id,
            perspective_id=r.perspective_id,
            taken_at=r.taken_at,
            member_count=len(_parse_membership(r.membership)),
        )
        for r in rows
    ]

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
def get_snapshot(
    perspective_id: str,
    snapshot_id: int,
    db: Session = Depends(get_session),
) -> SnapshotDetailResponse:
    """Return a specific snapshot with server names resolved."""
    snapshot = (
        db.query(PerspectiveSnapshot)
        .filter(
            PerspectiveSnapshot.id == snapshot_id,
            PerspectiveSnapshot.perspective_id == perspective_id,
        )
        .first()
    )
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    perspective = (
        db.query(Perspective)
        .filter(Perspective.id == perspective_id)
        .first()
    )
    perspective_name = perspective.name if perspective else ""

    membership = _parse_membership(snapshot.membership)
    server_ids = list(membership.keys())
    names = _server_names(db, server_ids)

    members = [
        SnapshotMember(server_id=sid, server_name=names.get(sid), tier=tier)
        for sid, tier in membership.items()
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
    "/perspectives/{perspective_id}/snapshots/{snapshot_id}/events",
    response_model=SnapshotEventsResponse,
)
def get_snapshot_events(
    perspective_id: str,
    snapshot_id: int,
    db: Session = Depends(get_session),
) -> SnapshotEventsResponse:
    """Return perspective events up to and including the snapshot's taken_at time."""
    snapshot = (
        db.query(PerspectiveSnapshot)
        .filter(
            PerspectiveSnapshot.id == snapshot_id,
            PerspectiveSnapshot.perspective_id == perspective_id,
        )
        .first()
    )
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    rows = (
        db.query(PerspectiveEvent)
        .filter(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.created_at <= snapshot.taken_at,
        )
        .order_by(desc(PerspectiveEvent.created_at))
        .limit(100)
        .all()
    )

    events = [
        SnapshotEvent(
            id=r.id,
            server_id=r.server_id,
            change_type=r.change_type,
            old_tier=r.old_tier,
            new_tier=r.new_tier,
            seen=r.seen,
            created_at=r.created_at,
        )
        for r in rows
    ]

    return SnapshotEventsResponse(
        perspective_id=perspective_id,
        snapshot_id=snapshot_id,
        snapshot_taken_at=snapshot.taken_at,
        events=events,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
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

    with TestSessionLocal() as sess:
        sess.add(Perspective(
            id="pid-test",
            org_id="org-100",
            name="Production Servers",
            facet_filters={},
            created_by="admin",
        ))
        sess.add(McpServerRegistry(
            server_id="srv-001",
            name="Server Alpha",
            registry_source="test",
            url="https://example.com",
        ))
        sess.add(McpServerRegistry(
            server_id="srv-002",
            name="Server Beta",
            registry_source="test",
            url="https://example2.com",
        ))
        sess.add(McpServerRegistry(
            server_id="srv-003",
            name="Server Gamma",
            registry_source="test",
            url="https://example3.com",
        ))
        snap1 = PerspectiveSnapshot(
            perspective_id="pid-test",
            taken_at=datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            membership={"srv-001": "low", "srv-002": "medium"},
        )
        snap2 = PerspectiveSnapshot(
            perspective_id="pid-test",
            taken_at=datetime(2024, 2, 1, 10, 0, 0, tzinfo=timezone.utc),
            membership={"srv-001": "high", "srv-002": "medium", "srv-003": "low"},
        )
        sess.add_all([snap1, snap2])
        sess.add(PerspectiveEvent(
            perspective_id="pid-test",
            server_id="srv-001",
            change_type="tier_changed",
            old_tier="low",
            new_tier="high",
            seen=True,
            created_at=datetime(2024, 2, 1, 9, 0, 0, tzinfo=timezone.utc),
        ))
        sess.add(PerspectiveEvent(
            perspective_id="pid-test",
            server_id="srv-003",
            change_type="entered",
            old_tier=None,
            new_tier="low",
            seen=False,
            created_at=datetime(2024, 2, 1, 10, 30, 0, tzinfo=timezone.utc),
        ))
        sess.commit()

    client = TestClient(app)

    # Test 1: list snapshots
    resp = client.get("/api/perspectives/pid-test/snapshots?limit=10&offset=0")
    if resp.status_code != 200:
        print(f"FAIL: list snapshots returned {resp.status_code}")
        sys.exit(1)
    data = resp.json()
    if data["perspective_id"] != "pid-test":
        print(f"FAIL: perspective_id mismatch")
        sys.exit(1)
    if data["perspective_name"] != "Production Servers":
        print(f"FAIL: perspective_name mismatch")
        sys.exit(1)
    if data["total"] != 2:
        print(f"FAIL: expected total=2, got {data['total']}")
        sys.exit(1)
    if len(data["snapshots"]) != 2:
        print(f"FAIL: expected 2 snapshots, got {len(data['snapshots'])}")
        sys.exit(1)

    # Test 2: snapshot detail with server names
    resp2 = client.get("/api/perspectives/pid-test/snapshots/1")
    if resp2.status_code != 200:
        print(f"FAIL: get snapshot returned {resp2.status_code}")
        sys.exit(1)
    detail = resp2.json()
    if detail["member_count"] != 2:
        print(f"FAIL: expected 2 members, got {detail['member_count']}")
        sys.exit(1)
    if detail["perspective_name"] != "Production Servers":
        print(f"FAIL: perspective_name in detail mismatch")
        sys.exit(1)
    names_map = {m["server_id"]: m["server_name"] for m in detail["members"]}
    if names_map.get("srv-001") != "Server Alpha":
        print(f"FAIL: srv-001 name should be 'Server Alpha', got {names_map.get('srv-001')}")
        sys.exit(1)

    # Test 3: snapshot events
    resp3 = client.get("/api/perspectives/pid-test/snapshots/1/events")
    if resp3.status_code != 200:
        print(f"FAIL: snapshot events returned {resp3.status_code}")
        sys.exit(1)
    events_data = resp3.json()
    if len(events_data["events"]) != 2:
        print(f"FAIL: expected 2 events, got {len(events_data['events'])}")
        sys.exit(1)
    if events_data["perspective_id"] != "pid-test":
        print(f"FAIL: perspective_id in events response mismatch")
        sys.exit(1)

    # Test 4: nonexistent snapshot -> 404
    resp4 = client.get("/api/perspectives/pid-test/snapshots/99999")
    if resp4.status_code != 404:
        print(f"FAIL: nonexistent snapshot should be 404, got {resp4.status_code}")
        sys.exit(1)

    # Test 5: nonexistent perspective -> 404
    resp5 = client.get("/api/perspectives/nonexistent/snapshots")
    if resp5.status_code != 404:
        print(f"FAIL: nonexistent perspective should be 404, got {resp5.status_code}")
        sys.exit(1)

    # Test 6: pagination
    resp6 = client.get("/api/perspectives/pid-test/snapshots?limit=1&offset=0")
    if resp6.status_code != 200:
        print(f"FAIL: paginated list returned {resp6.status_code}")
        sys.exit(1)
    if len(resp6.json()["snapshots"]) != 1:
        print(f"FAIL: limit=1 should return 1 snapshot")
        sys.exit(1)
    if resp6.json()["total"] != 2:
        print(f"FAIL: total should still be 2 with pagination")
        sys.exit(1)

    print("PASS")
