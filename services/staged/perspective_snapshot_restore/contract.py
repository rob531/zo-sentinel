"""
services.staged.perspective_snapshot_restore.contract

FastAPI contract for listing perspective snapshots and restoring a perspective
to a prior snapshot.
"""

from datetime import datetime
from typing import List, Dict, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import select, insert
from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import get_session
from app.models import (
    Perspective,
    PerspectiveSnapshot,
    PerspectiveEvent,
)

router = APIRouter(prefix="/api/perspectives", tags=["perspective_snapshot_restore"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class SnapshotItem(BaseModel):
    id: int
    taken_at: datetime
    membership: Dict[str, Any]


class SnapshotsResponse(BaseModel):
    snapshots: List[SnapshotItem]


class RestoreRequest(BaseModel):
    snapshot_id: int = Field(..., description="ID of the snapshot to restore")


class RestoreResponse(BaseModel):
    perspective_id: str
    restored_to: int
    membership: Dict[str, Any]


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
def _get_perspective(session: Session, perspective_id: str) -> Perspective:
    stmt = select(Perspective).where(Perspective.id == perspective_id)
    perspective = session.execute(stmt).scalar_one_or_none()
    if not perspective:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Perspective not found")
    return perspective


def _get_snapshot(session: Session, snapshot_id: int) -> PerspectiveSnapshot:
    stmt = select(PerspectiveSnapshot).where(PerspectiveSnapshot.id == snapshot_id)
    snapshot = session.execute(stmt).scalar_one_or_none()
    if not snapshot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")
    return snapshot


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get("/{perspective_id}/snapshots", response_model=SnapshotsResponse)
def list_snapshots(
    perspective_id: str,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
):
    # Ensure perspective exists and belongs to the caller's org (org scoping omitted for brevity)
    _ = _get_perspective(session, perspective_id)

    stmt = (
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = session.execute(stmt).scalars().all()
    snapshots = [
        SnapshotItem(
            id=row.id,
            taken_at=row.taken_at,
            membership=row.membership,
        )
        for row in rows
    ]
    return SnapshotsResponse(snapshots=snapshots)


@router.post("/{perspective_id}/restore", response_model=RestoreResponse)
def restore_snapshot(
    perspective_id: str,
    payload: RestoreRequest,
    session: Session = Depends(get_session),
):
    perspective = _get_perspective(session, perspective_id)
    snapshot = _get_snapshot(session, payload.snapshot_id)

    if snapshot.perspective_id != perspective_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Snapshot does not belong to the specified perspective",
        )

    # Insert events for each membership entry (simple restore semantics)
    now = datetime.utcnow()
    events = []
    for server_id, tier in snapshot.membership.items():
        events.append(
            {
                "perspective_id": perspective_id,
                "server_id": server_id,
                "change_type": "restore",
                "old_tier": None,
                "new_tier": tier,
                "seen": False,
                "created_at": now,
            }
        )
    if events:
        stmt = insert(PerspectiveEvent)
        session.execute(stmt, events)

    # Optionally, update perspective's updated_at (if column exists)
    if hasattr(Perspective, "updated_at"):
        perspective.updated_at = now

    session.commit()

    return RestoreResponse(
        perspective_id=perspective_id,
        restored_to=snapshot.id,
        membership=snapshot.membership,
    )


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build a temporary FastAPI app with an in‑memory SQLite DB
    app = FastAPI()
    app.include_router(router)

    # ------------------------------------------------------------------- #
    # Dependency override: provide a SQLite in‑memory session
    # ------------------------------------------------------------------- #
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    TEST_ENGINE = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=TEST_ENGINE, autoflush=False, autocommit=False)

    def override_get_session() -> Session:  # pragma: no cover
        return TestSessionLocal()

    # Create tables using the metadata attached to the models
    Perspective.__table__.metadata.create_all(bind=TEST_ENGINE)

    app.dependency_overrides[get_session] = override_get_session

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    with TestSessionLocal() as db:
        # Perspectives
        p1 = Perspective(
            id="p1",
            org_id="org1",
            name="Perspective One",
            description="",
            facet_filters={},
            created_by="tester",
            created_at=datetime.utcnow(),
        )
        p2 = Perspective(
            id="p2",
            org_id="org1",
            name="Perspective Two",
            description="",
            facet_filters={},
            created_by="tester",
            created_at=datetime.utcnow(),
        )
        db.add_all([p1, p2])
        db.flush()

        # Snapshots for p1
        snapshots = [
            PerspectiveSnapshot(
                id=1,
                perspective_id="p1",
                taken_at=datetime.utcnow(),
                membership={"srvA": "high"},
            ),
            PerspectiveSnapshot(
                id=2,
                perspective_id="p1",
                taken_at=datetime.utcnow(),
                membership={"srvA": "medium", "srvB": "low"},
            ),
            PerspectiveSnapshot(
                id=3,
                perspective_id="p1",
                taken_at=datetime.utcnow(),
                membership={"srvC": "critical"},
            ),
        ]
        db.add_all(snapshots)
        db.commit()

    # ------------------------------------------------------------------- #
    # Run tests
    # ------------------------------------------------------------------- #
    client = TestClient(app)

    # GET snapshots
    resp = client.get("/api/perspectives/p1/snapshots")
    assert resp.status_code == 200, f"GET snapshots failed: {resp.text}"
    data = resp.json()
    assert "snapshots" in data, "Missing snapshots key"
    assert len(data["snapshots"]) == 3, f"Expected 3 snapshots, got {len(data['snapshots'])}"

    # POST restore
    resp = client.post(
        "/api/perspectives/p1/restore",
        json={"snapshot_id": 1},
    )
    assert resp.status_code == 200, f"POST restore failed: {resp.text}"
    data = resp.json()
    assert data.get("restored_to") == 1, "Restored_to mismatch"

    print("PASS")
    raise SystemExit(0)