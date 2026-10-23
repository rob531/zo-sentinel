"""
services.staged.perspective_history.contract
"""

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Real data layer imports (must not be stubbed)
from app.db import get_session, Base
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot

router = APIRouter(prefix="/api")


# ---------- Pydantic response models ----------
class SnapshotOut(BaseModel):
    taken_at: datetime
    membership: Dict  # JSON column, keep as generic dict


class EventOut(BaseModel):
    server_id: int
    change_type: str
    old_tier: Optional[int] = None
    new_tier: Optional[int] = None
    created_at: datetime


class SummaryOut(BaseModel):
    total_servers: int
    tier_distribution: Dict[str, int]


class HistoryResponse(BaseModel):
    perspective_id: int
    snapshots: List[SnapshotOut]
    events: List[EventOut]
    summary: SummaryOut


# ---------- Core logic ----------
def _fetch_snapshots(db: Session, perspective_id: int) -> List[PerspectiveSnapshot]:
    return (
        db.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.desc())
        .all()
    )


def _fetch_events(db: Session, perspective_id: int) -> List[PerspectiveEvent]:
    return (
        db.query(PerspectiveEvent)
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(PerspectiveEvent.created_at.desc())
        .all()
    )


def _build_summary(events: List[PerspectiveEvent]) -> SummaryOut:
    server_ids = {e.server_id for e in events}
    tier_counts: Dict[str, int] = {}
    for ev in events:
        tier_key = str(ev.new_tier) if ev.new_tier is not None else "None"
        tier_counts[tier_key] = tier_counts.get(tier_key, 0) + 1
    return SummaryOut(
        total_servers=len(server_ids),
        tier_distribution=tier_counts,
    )


@router.get(
    "/perspectives/{perspective_id}/history",
    response_model=HistoryResponse,
    name="get_perspective_history",
)
def get_perspective_history(
    perspective_id: int, db: Session = Depends(get_session)
) -> HistoryResponse:
    # Ensure the perspective exists (will raise 404 if not)
    db.query(Perspective).filter(Perspective.id == perspective_id).first()

    snapshots = _fetch_snapshots(db, perspective_id)
    events = _fetch_events(db, perspective_id)

    snapshot_out = [
        SnapshotOut(taken_at=s.taken_at, membership=s.membership) for s in snapshots
    ]
    event_out = [
        EventOut(
            server_id=e.server_id,
            change_type=e.change_type,
            old_tier=e.old_tier,
            new_tier=e.new_tier,
            created_at=e.created_at,
        )
        for e in events
    ]

    summary = _build_summary(events)

    return HistoryResponse(
        perspective_id=perspective_id,
        snapshots=snapshot_out,
        events=event_out,
        summary=summary,
    )


# ---------- Self‑test ----------
if __name__ == "__main__":

    # Build a minimal FastAPI app for the self‑test
    app = FastAPI()
    app.include_router(router)

    # ------------------------------------------------------------------
    # Override the real DB session with an in‑memory SQLite session
    # ------------------------------------------------------------------
    TEST_DATABASE_URL = "sqlite:///:memory:"

    test_engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create tables
    Base.metadata.create_all(bind=test_engine)

    # Seed test data
    with TestSessionLocal() as db:
        # Perspective
        perspective = Perspective(
            id=1,
            name="Test Perspective",
            description="",
            facet_filters="{}",
            org_id=1,
            created_by=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(perspective)

        # Snapshots
        snap1 = PerspectiveSnapshot(
            id=1,
            perspective_id=1,
            taken_at=datetime(2023, 1, 1, 12, 0, 0),
            membership={"serverA": {"tier": 1}},
        )
        snap2 = PerspectiveSnapshot(
            id=2,
            perspective_id=1,
            taken_at=datetime(2023, 1, 2, 12, 0, 0),
            membership={"serverA": {"tier": 2}, "serverB": {"tier": 1}},
        )
        db.add_all([snap1, snap2])

        # Events
        ev1 = PerspectiveEvent(
            id=1,
            perspective_id=1,
            server_id=1,
            change_type="upgrade",
            old_tier=1,
            new_tier=2,
            seen=False,
            created_at=datetime(2023, 1, 3, 9, 0, 0),
        )
        ev2 = PerspectiveEvent(
            id=2,
            perspective_id=1,
            server_id=2,
            change_type="downgrade",
            old_tier=2,
            new_tier=1,
            seen=False,
            created_at=datetime(2023, 1, 4, 10, 0, 0),
        )
        ev3 = PerspectiveEvent(
            id=3,
            perspective_id=1,
            server_id=1,
            change_type="reassign",
            old_tier=2,
            new_tier=3,
            seen=False,
            created_at=datetime(2023, 1, 5, 11, 0, 0),
        )
        db.add_all([ev1, ev2, ev3])

        db.commit()

    # Run the test client
    client = TestClient(app)
    response = client.get("/api/perspectives/1/history")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()
    assert data["perspective_id"] == 1
    assert len(data["snapshots"]) == 2, "Expected 2 snapshots"
    assert "tier_distribution" in data["summary"], "Missing tier_distribution"
    print("PASS")