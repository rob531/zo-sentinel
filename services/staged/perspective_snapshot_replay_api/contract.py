# services/staged/perspective_snapshot_replay_api/contract.py
from datetime import datetime
from typing import List, Dict, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from sqlalchemy import select, asc
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from app.db import get_session, Base
from app.models import Perspective, PerspectiveSnapshot, PerspectiveEvent

router = APIRouter()


@router.get(
    "/api/perspectives/{perspective_id}/replay",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def get_perspective_replay(
    perspective_id: int, db: Session = Depends(get_session)
) -> Dict[str, Any]:
    # fetch perspective
    perspective = db.execute(
        select(Perspective).where(Perspective.id == perspective_id)
    ).scalar_one_or_none()
    if perspective is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Perspective {perspective_id} not found",
        )

    # fetch snapshots ordered by taken_at
    snapshots_rows = db.execute(
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(asc(PerspectiveSnapshot.taken_at))
    ).scalars().all()

    # build snapshot view
    snapshots: List[Dict[str, Any]] = []
    prev_membership: set = set()
    for snap in snapshots_rows:
        # assume membership is stored as a JSON-serialised list
        current_membership = set(snap.membership or [])
        entered = list(current_membership - prev_membership)
        exited = list(prev_membership - current_membership)
        snapshots.append(
            {
                "taken_at": snap.taken_at.isoformat(),
                "member_count": len(current_membership),
                "entered": entered,
                "exited": exited,
            }
        )
        prev_membership = current_membership

    # fetch events
    events_rows = db.execute(
        select(PerspectiveEvent).where(PerspectiveEvent.perspective_id == perspective_id)
    ).scalars().all()
    event_count = len(events_rows)

    # compute unmatched events (servers never appearing in any snapshot)
    all_snapshot_members = set()
    for snap in snapshots_rows:
        all_snapshot_members.update(snap.membership or [])
    unmatched_events = [
        ev.server_id for ev in events_rows if ev.server_id not in all_snapshot_members
    ]

    return {
        "perspective_id": perspective.id,
        "name": perspective.name,
        "snapshots": snapshots,
        "event_count": event_count,
        "unmatched_events": unmatched_events,
    }


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import scoped_session

    # create in‑memory SQLite engine
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = scoped_session(sessionmaker(bind=engine))

    # dependency override
    def get_test_session() -> Session:
        return TestSessionLocal()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # seed data
    with TestSessionLocal() as db:
        # perspective
        perspective = Perspective(
            id=1,
            org_id=1,
            name="Test Perspective",
            facet_filters="[]",
            created_by=1,
            description="",
        )
        db.add(perspective)

        # snapshots
        snap1 = PerspectiveSnapshot(
            id=1,
            perspective_id=1,
            taken_at=datetime(2023, 1, 1, 0, 0, 0),
            membership=[1, 2, 3],
        )
        snap2 = PerspectiveSnapshot(
            id=2,
            perspective_id=1,
            taken_at=datetime(2023, 1, 2, 0, 0, 0),
            membership=[1, 2, 3, 4, 5],
        )
        db.add_all([snap1, snap2])

        # events
        ev1 = PerspectiveEvent(
            id=1,
            perspective_id=1,
            server_id=4,
            change_type="added",
            old_tier=None,
            new_tier="gold",
            seen=False,
            created_at=datetime.utcnow(),
        )
        ev2 = PerspectiveEvent(
            id=2,
            perspective_id=1,
            server_id=5,
            change_type="added",
            old_tier=None,
            new_tier="silver",
            seen=False,
            created_at=datetime.utcnow(),
        )
        db.add_all([ev1, ev2])

        db.commit()

    client = TestClient(app)
    resp = client.get("/api/perspectives/1/replay")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert len(data["snapshots"]) == 2, "Expected 2 snapshots"
    assert len(data["snapshots"][0]["entered"]) == 3, "First snapshot entered count mismatch"
    assert len(data["snapshots"][1]["entered"]) == 2, "Second snapshot entered count mismatch"
    print("PASS")