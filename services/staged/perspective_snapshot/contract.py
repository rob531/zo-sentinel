"""
services.staged.perspective_snapshot.contract
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Real data layer imports (must not be re‑implemented)
from app.db import get_session
from app.models import PerspectiveSnapshot, Base  # type: ignore

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class MembershipItem(BaseModel):
    server_id: int
    risk_tier: str


class SnapshotModel(BaseModel):
    taken_at: datetime
    membership: List[MembershipItem]


class SnapshotResponse(BaseModel):
    snapshot: SnapshotModel


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
@router.get(
    "/perspectives/{perspective_id}/snapshot",
    response_model=SnapshotResponse,
    name="perspective_snapshot:get_snapshot",
)
def get_snapshot(
    perspective_id: int,
    db: Session = Depends(get_session),
) -> SnapshotResponse:
    """
    Return the most recent snapshot for the given perspective.
    """
    snap = (
        db.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.desc())
        .first()
    )
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    # `membership` is stored as JSON; pydantic will validate the structure.
    return SnapshotResponse(
        snapshot=SnapshotModel(
            taken_at=snap.taken_at,
            membership=[MembershipItem(**item) for item in snap.membership],
        )
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.perspective_snapshot.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ----------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mimics the real models
    # ----------------------------------------------------------------------- #
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    TEST_ENGINE = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=TEST_ENGINE
    )

    # Create tables
    Base.metadata.create_all(bind=TEST_ENGINE)

    # Seed a snapshot
    test_snapshot = PerspectiveSnapshot(
        perspective_id=1,
        taken_at=datetime.utcnow(),
        membership=[{"server_id": 42, "risk_tier": "high"}],
    )
    with TestSessionLocal() as db:
        db.add(test_snapshot)
        db.commit()

    # ----------------------------------------------------------------------- #
    # FastAPI app for the test
    # ----------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # Override the real DB dependency with the in‑memory one
    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # ----------------------------------------------------------------------- #
    # Execute the test
    # ----------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get("/api/perspectives/1/snapshot")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "snapshot" in data, "Missing snapshot key"
    snap = data["snapshot"]
    assert "taken_at" in snap, "Missing taken_at"
    assert isinstance(snap["membership"], list), "membership not a list"
    assert snap["membership"][0]["server_id"] == 42, "Wrong server_id"
    assert snap["membership"][0]["risk_tier"] == "high", "Wrong risk_tier"

    print("PASS")