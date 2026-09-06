# deps: fastapi, sqlalchemy, pydantic
"""perspective_timeline — public API for perspective timeline and snapshot data.

GET  /api/perspectives                          List perspectives (paginated).
GET  /api/perspectives/{perspective_id}        Get a single perspective.
GET  /api/perspectives/{perspective_id}/timeline  List timeline events for a perspective.
GET  /api/perspectives/{perspective_id}/snapshots  List snapshots for a perspective.

Auth: public.
Data: app tier via get_session + Perspective + PerspectiveEvent + PerspectiveSnapshot
      + McpServerRegistry.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveEvent, PerspectiveSnapshot

router = APIRouter(prefix="/api", tags=["perspective_timeline"])


# --------------------------------------------------------------------------- #
# Response shapes
# --------------------------------------------------------------------------- #


class ServerRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: int
    name: Optional[str] = None
    risk_tier: Optional[str] = None


class TimelineEventItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    perspective_id: int
    server_id: int
    change_type: str
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    seen: bool
    created_at: datetime
    server: Optional[ServerRef] = None


class TimelineResponse(BaseModel):
    perspective_id: int
    events: list[TimelineEventItem]
    total: int


class SnapshotItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    perspective_id: int
    taken_at: datetime
    membership: Optional[dict] = None


class PerspectiveItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    org_id: Optional[int] = None
    name: str
    description: Optional[str] = None
    facet_filters: Optional[dict] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class PerspectiveDetail(PerspectiveItem):
    event_count: int = 0
    snapshot_count: int = 0


class PerspectiveListResponse(BaseModel):
    perspectives: list[PerspectiveItem]
    total: int


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _server_ref(row: PerspectiveEvent, db: Session) -> Optional[ServerRef]:
    """Attach server name and risk_tier when available."""
    if row.server_id is None:
        return None
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == row.server_id
    ).first()
    if server:
        return ServerRef(
            server_id=server.server_id,
            name=server.name,
            risk_tier=server.risk_tier,
        )
    return None


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get("/perspectives", response_model=PerspectiveListResponse)
def list_perspectives(
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    org_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_session),
) -> PerspectiveListResponse:
    """Return a paginated list of perspectives, optionally filtered by org."""
    stmt = select(Perspective)
    count_stmt = select(func.count(Perspective.id))

    if org_id is not None:
        stmt = stmt.filter(Perspective.org_id == org_id)
        count_stmt = count_stmt.filter(Perspective.org_id == org_id)

    total = db.execute(count_stmt).scalar() or 0
    stmt = stmt.order_by(Perspective.created_at.desc()).offset(offset).limit(limit)
    rows = db.execute(stmt).scalars().all()

    return PerspectiveListResponse(
        perspectives=[PerspectiveItem.model_validate(r) for r in rows],
        total=total,
    )


@router.get("/perspectives/{perspective_id}", response_model=PerspectiveDetail)
def get_perspective(
    perspective_id: int,
    db: Session = Depends(get_session),
) -> PerspectiveDetail:
    """Return a single perspective with event and snapshot counts."""
    perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail=f"Perspective {perspective_id} not found")

    event_count = db.query(func.count(PerspectiveEvent.id)).filter(
        PerspectiveEvent.perspective_id == perspective_id
    ).scalar() or 0

    snapshot_count = db.query(func.count(PerspectiveSnapshot.id)).filter(
        PerspectiveSnapshot.perspective_id == perspective_id
    ).scalar() or 0

    return PerspectiveDetail(
        model_validate=PerspectiveItem.model_validate(perspective),
        event_count=event_count,
        snapshot_count=snapshot_count,
    )


@router.get("/perspectives/{perspective_id}/timeline", response_model=TimelineResponse)
def get_perspective_timeline(
    perspective_id: int,
    change_type: Optional[str] = Query(default=None, description="Filter by change_type"),
    start_date: Optional[datetime] = Query(default=None, description="Filter from this date"),
    end_date: Optional[datetime] = Query(default=None, description="Filter until this date"),
    limit: int = Query(default=200, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> TimelineResponse:
    """Return chronological timeline events for a perspective."""
    stmt = select(PerspectiveEvent).filter(PerspectiveEvent.perspective_id == perspective_id)
    count_stmt = select(func.count(PerspectiveEvent.id)).filter(
        PerspectiveEvent.perspective_id == perspective_id
    )

    if change_type is not None:
        stmt = stmt.filter(PerspectiveEvent.change_type == change_type)
        count_stmt = count_stmt.filter(PerspectiveEvent.change_type == change_type)
    if start_date is not None:
        stmt = stmt.filter(PerspectiveEvent.created_at >= start_date)
        count_stmt = count_stmt.filter(PerspectiveEvent.created_at >= start_date)
    if end_date is not None:
        stmt = stmt.filter(PerspectiveEvent.created_at <= end_date)
        count_stmt = count_stmt.filter(PerspectiveEvent.created_at <= end_date)

    total = db.execute(count_stmt).scalar() or 0
    stmt = stmt.order_by(PerspectiveEvent.created_at.asc()).offset(offset).limit(limit)
    rows = db.execute(stmt).scalars().all()

    events = []
    for row in rows:
        server_ref = _server_ref(row, db)
        events.append(TimelineEventItem(
            id=row.id,
            perspective_id=row.perspective_id,
            server_id=row.server_id,
            change_type=row.change_type,
            old_tier=row.old_tier,
            new_tier=row.new_tier,
            seen=row.seen,
            created_at=row.created_at,
            server=server_ref,
        ))

    return TimelineResponse(perspective_id=perspective_id, events=events, total=total)


@router.get("/perspectives/{perspective_id}/snapshots", response_model=list[SnapshotItem])
def get_perspective_snapshots(
    perspective_id: int,
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> list[SnapshotItem]:
    """Return snapshots taken for a perspective."""
    perspective = db.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail=f"Perspective {perspective_id} not found")

    stmt = (
        select(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = db.execute(stmt).scalars().all()
    return [SnapshotItem.model_validate(r) for r in rows]


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    # Seed data
    with TestingSessionLocal() as db:
        now = datetime.utcnow()

        srv1 = McpServerRegistry(server_id=1, name="server-alpha", risk_tier="medium")
        srv2 = McpServerRegistry(server_id=2, name="server-beta", risk_tier="high")
        db.add_all([srv1, srv2])

        p1 = Perspective(
            id=1,
            org_id=1,
            name="persp-one",
            description="First test perspective",
            facet_filters={"severity": ["high"]},
            created_by="tester",
            created_at=now,
            updated_at=now,
        )
        p2 = Perspective(
            id=2,
            org_id=1,
            name="persp-two",
            description="Second test perspective",
            created_by="tester",
            created_at=now,
            updated_at=now,
        )
        db.add_all([p1, p2])

        snap1 = PerspectiveSnapshot(perspective_id=1, taken_at=now, membership={"count": 3})
        db.add(snap1)

        ev1 = PerspectiveEvent(
            perspective_id=1, server_id=1, change_type="tier_upgrade",
            old_tier="low", new_tier="medium", seen=True, created_at=now,
        )
        ev2 = PerspectiveEvent(
            perspective_id=1, server_id=2, change_type="tier_downgrade",
            old_tier="high", new_tier="medium", seen=True,
            created_at=datetime.fromtimestamp(now.timestamp() + 3600),
        )
        ev3 = PerspectiveEvent(
            perspective_id=1, server_id=1, change_type="new_server",
            old_tier=None, new_tier="low", seen=True,
            created_at=datetime.fromtimestamp(now.timestamp() + 7200),
        )
        ev4 = PerspectiveEvent(
            perspective_id=2, server_id=2, change_type="tier_upgrade",
            old_tier="low", new_tier="high", seen=True, created_at=now,
        )
        db.add_all([ev1, ev2, ev3, ev4])
        db.commit()

    # Test list perspectives
    resp = client.get("/api/perspectives")
    assert resp.status_code == 200, f"list_perspectives: {resp.status_code}"
    data = resp.json()
    assert data["total"] == 2, f"total=2 expected, got {data['total']}"
    assert len(data["perspectives"]) == 2

    # Test list with org filter
    resp = client.get("/api/perspectives?org_id=1")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2

    # Test list with org filter (no match)
    resp = client.get("/api/perspectives?org_id=999")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

    # Test get single perspective
    resp = client.get("/api/perspectives/1")
    assert resp.status_code == 200, f"get_perspective: {resp.status_code}"
    d = resp.json()
    assert d["name"] == "persp-one"
    assert d["event_count"] == 3
    assert d["snapshot_count"] == 1

    # Test 404
    resp = client.get("/api/perspectives/9999")
    assert resp.status_code == 404

    # Test timeline
    resp = client.get("/api/perspectives/1/timeline")
    assert resp.status_code == 200, f"timeline: {resp.status_code}"
    data = resp.json()
    assert data["perspective_id"] == 1
    assert data["total"] == 3
    assert len(data["events"]) == 3
    # First event has server info
    first = data["events"][0]
    assert "server_id" in first
    assert "change_type" in first
    assert "created_at" in first
    assert first["server"] is not None
    assert first["server"]["name"] == "server-alpha"

    # Test timeline with change_type filter
    resp = client.get("/api/perspectives/1/timeline?change_type=tier_upgrade")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["events"][0]["change_type"] == "tier_upgrade"

    # Test timeline for perspective 2
    resp = client.get("/api/perspectives/2/timeline")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    # Test snapshots
    resp = client.get("/api/perspectives/1/snapshots")
    assert resp.status_code == 200, f"snapshots: {resp.status_code}"
    data = resp.json()
    assert len(data) == 1
    assert data[0]["perspective_id"] == 1

    # Test snapshots 404
    resp = client.get("/api/perspectives/9999/snapshots")
    assert resp.status_code == 404

    print("PASS")
    sys.exit(0)
