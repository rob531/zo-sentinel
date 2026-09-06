"""
services.staged.perspective_snapshot.contract
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Real data layer imports (must stay unchanged)
from app.db import Base, get_session
from app.models import (
    Perspective,
    PerspectiveEvent,
    PerspectiveSnapshot,
)

router = APIRouter(prefix="/api")


class SnapshotResponse(BaseModel):
    snapshot_id: int
    taken_at: datetime


@router.post(
    "/perspectives/{perspective_id}/snapshot",
    response_model=SnapshotResponse,
    status_code=200,
)
def create_snapshot(
    perspective_id: int,
    session: Session = Depends(get_session),
):
    # Verify the perspective exists
    perspective = session.get(Perspective, perspective_id)
    if perspective is None:
        raise HTTPException(status_code=404, detail="Perspective not found")

    # Determine current membership (distinct server ids from events)
    server_ids = (
        session.query(PerspectiveEvent.server_id)
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .distinct()
        .all()
    )
    membership: List[int] = [sid for (sid,) in server_ids]

    # Create snapshot record
    snapshot = PerspectiveSnapshot(
        perspective_id=perspective_id,
        membership=membership,
        taken_at=datetime.utcnow(),
    )
    session.add(snapshot)
    session.commit()
    session.refresh(snapshot)

    return SnapshotResponse(snapshot_id=snapshot.id, taken_at=snapshot.taken_at)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.perspective_snapshot.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    # ------------------------------------------------------------------- #
    # Build a minimal FastAPI app with the router and override the DB layer
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # In‑memory SQLite engine (StaticPool) for the test
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Override the dependency used by the router
    def _override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _override_get_session

    # Create tables
    Base.metadata.create_all(bind=test_engine)

    # ------------------------------------------------------------------- #
    # Seed minimal data: a perspective with two server events
    # ------------------------------------------------------------------- #
    with TestSessionLocal() as db:
        perspective = Perspective(
            id=1,
            name="test-perspective",
            description="test",
            facet_filters="{}",
            org_id=1,
            created_by=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(perspective)

        event1 = PerspectiveEvent(
            id=1,
            perspective_id=1,
            server_id=10,
            change_type="add",
            old_tier=None,
            new_tier=None,
            seen=False,
            created_at=datetime.utcnow(),
        )
        event2 = PerspectiveEvent(
            id=2,
            perspective_id=1,
            server_id=20,
            change_type="add",
            old_tier=None,
            new_tier=None,
            seen=False,
            created_at=datetime.utcnow(),
        )
        db.add_all([event1, event2])
        db.commit()

    # ------------------------------------------------------------------- #
    # Run the acceptance test
    # ------------------------------------------------------------------- #
    client = TestClient(app)

    resp = client.post("/api/perspectives/1/snapshot")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "snapshot_id" in data and isinstance(data["snapshot_id"], int)
    assert "taken_at" in data

    print("PASS")
    raise SystemExit(0)