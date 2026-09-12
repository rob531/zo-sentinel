import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Real data layer imports (must remain unchanged)
from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot

router = APIRouter(prefix="/api")


# ---------- Pydantic response models ----------
class SnapshotModel(BaseModel):
    id: int
    taken_at: datetime
    membership_count: int = Field(..., alias="membership_count")


class EventModel(BaseModel):
    id: int
    server_id: int
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: datetime


class ServerModel(BaseModel):
    server_id: int
    name: str
    risk_tier: str


class PerspectiveTreeResponse(BaseModel):
    perspective_id: int
    name: str
    description: Optional[str]
    snapshot: SnapshotModel
    events: List[EventModel]
    servers: List[ServerModel]


# ---------- Endpoint implementation ----------
@router.get(
    "/perspectives/{perspective_id}/tree",
    response_model=PerspectiveTreeResponse,
    name="get_perspective_tree",
)
def get_perspective_tree(
    perspective_id: int, db: Session = Depends(get_session)
) -> PerspectiveTreeResponse:
    # Fetch perspective
    perspective = db.get(Perspective, perspective_id)
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")

    # Latest snapshot (by taken_at)
    snapshot_stmt = (
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(desc(PerspectiveSnapshot.taken_at))
        .limit(1)
    )
    snapshot = db.execute(snapshot_stmt).scalar_one_or_none()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    # Un‑seen events for this perspective
    events_stmt = (
        select(PerspectiveEvent)
        .where(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.seen.is_(False),
        )
        .order_by(PerspectiveEvent.created_at)
    )
    events = db.execute(events_stmt).scalars().all()

    # Parse membership (assumed JSON list of server dicts)
    try:
        membership = json.loads(snapshot.membership)
    except Exception:
        membership = []

    servers = [
        ServerModel(
            server_id=sv["server_id"],
            name=sv.get("name", ""),
            risk_tier=sv.get("risk_tier", ""),
        )
        for sv in membership
    ]

    snapshot_model = SnapshotModel(
        id=snapshot.id,
        taken_at=snapshot.taken_at,
        membership_count=len(membership),
    )

    event_models = [
        EventModel(
            id=e.id,
            server_id=e.server_id,
            change_type=e.change_type,
            old_tier=e.old_tier,
            new_tier=e.new_tier,
            seen=e.seen,
            created_at=e.created_at,
        )
        for e in events
    ]

    return PerspectiveTreeResponse(
        perspective_id=perspective.id,
        name=perspective.name,
        description=perspective.description,
        snapshot=snapshot_model,
        events=event_models,
        servers=servers,
    )


# ---------- FastAPI app ----------
app = FastAPI()
app.include_router(router)


# ---------- Self‑test (run as module) ----------
if __name__ == "__main__":

    # Create an in‑memory SQLite engine with a StaticPool
    engine: Engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables
    Perspective.metadata.create_all(engine)
    PerspectiveSnapshot.metadata.create_all(engine)
    PerspectiveEvent.metadata.create_all(engine)

    # Seed data
    db: Session = SessionLocal()
    now = datetime.utcnow()

    # Perspective
    perspective = Perspective(
        id=1,
        name="Test Perspective",
        description="A test perspective",
        created_at=now,
        updated_at=now,
        created_by=1,
        org_id=1,
        facet_filters="{}",
    )
    db.add(perspective)

    # Snapshot with two servers
    membership_data = [
        {"server_id": 1, "name": "ServerA", "risk_tier": "low"},
        {"server_id": 2, "name": "ServerB", "risk_tier": "medium"},
    ]
    snapshot = PerspectiveSnapshot(
        id=1,
        perspective_id=1,
        taken_at=now,
        membership=json.dumps(membership_data),
    )
    db.add(snapshot)

    # Two unseen events
    event1 = PerspectiveEvent(
        id=1,
        perspective_id=1,
        server_id=1,
        change_type="tier_change",
        old_tier="low",
        new_tier="high",
        seen=False,
        created_at=now,
    )
    event2 = PerspectiveEvent(
        id=2,
        perspective_id=1,
        server_id=2,
        change_type="tier_change",
        old_tier="medium",
        new_tier="high",
        seen=False,
        created_at=now,
    )
    db.add_all([event1, event2])
    db.commit()

    # Dependency override to use the in‑memory session
    def get_test_session() -> Session:
        return db

    app.dependency_overrides[get_session] = get_test_session

    # Run test client
    client = TestClient(app)
    resp = client.get("/api/perspectives/1/tree")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert len(data["events"]) == 2, "Expected 2 events"
    assert len(data["servers"]) == 2, "Expected 2 servers"

    print("PASS")