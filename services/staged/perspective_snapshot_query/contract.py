# services/staged/perspective_snapshot_query/contract.py
from datetime import datetime, timedelta
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, desc
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.db import Base, get_session
from app.models import Perspective, PerspectiveSnapshot

router = APIRouter(prefix="/api")


class SnapshotItem(BaseModel):
    id: int
    perspective_id: int
    taken_at: datetime
    membership: Any


class SnapshotResponse(BaseModel):
    snapshots: List[SnapshotItem]


@router.get(
    "/perspectives/{perspective_id}/snapshots",
    response_model=SnapshotResponse,
    name="perspective_snapshot_query:get_snapshots",
)
def get_snapshots(
    perspective_id: int,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    taken_after: Optional[datetime] = Query(None),
    session: Session = Depends(get_session),
) -> SnapshotResponse:
    """Return snapshots for a perspective, optionally filtered."""
    q = (
        session.query(PerspectiveSnapshot)
        .join(Perspective, Perspective.id == PerspectiveSnapshot.perspective_id)
        .filter(Perspective.id == perspective_id)
    )
    if taken_after:
        q = q.filter(PerspectiveSnapshot.taken_at > taken_after)
    q = q.order_by(desc(PerspectiveSnapshot.taken_at)).offset(offset).limit(limit)
    rows = q.all()
    return SnapshotResponse(
        snapshots=[
            SnapshotItem(
                id=row.id,
                perspective_id=row.perspective_id,
                taken_at=row.taken_at,
                membership=row.membership,
            )
            for row in rows
        ]
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.perspective_snapshot_query.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite app with the real models and session logic
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    # ------------------------------------------------------------------- #
    # Seed two perspectives, each with two snapshots
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    with SessionLocal() as db:
        p1 = Perspective(
            id=1,
            name="Perspective One",
            description="",
            facet_filters="{}",
            org_id=1,
            created_by=1,
            created_at=now - timedelta(days=2),
            updated_at=now - timedelta(days=2),
        )
        p2 = Perspective(
            id=2,
            name="Perspective Two",
            description="",
            facet_filters="{}",
            org_id=1,
            created_by=1,
            created_at=now - timedelta(days=2),
            updated_at=now - timedelta(days=2),
        )
        db.add_all([p1, p2])
        db.flush()  # ensure FK availability

        snapshots = [
            PerspectiveSnapshot(
                id=1,
                perspective_id=1,
                taken_at=now - timedelta(hours=5),
                membership="{}",
            ),
            PerspectiveSnapshot(
                id=2,
                perspective_id=1,
                taken_at=now - timedelta(hours=1),
                membership="{}",
            ),
            PerspectiveSnapshot(
                id=3,
                perspective_id=2,
                taken_at=now - timedelta(hours=3),
                membership="{}",
            ),
            PerspectiveSnapshot(
                id=4,
                perspective_id=2,
                taken_at=now - timedelta(hours=2),
                membership="{}",
            ),
        ]
        db.add_all(snapshots)
        db.commit()

    # ------------------------------------------------------------------- #
    # Dependency override for the test FastAPI app
    # ------------------------------------------------------------------- #
    def _override_get_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    # ------------------------------------------------------------------- #
    # Run the acceptance test
    # ------------------------------------------------------------------- #
    client = TestClient(app)

    resp = client.get("/api/perspectives/1/snapshots")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    snapshots = data.get("snapshots", [])
    assert snapshots, "No snapshots returned"
    # Verify ordering by taken_at descending
    taken_ats = [datetime.fromisoformat(s["taken_at"]) for s in snapshots]
    assert taken_ats == sorted(taken_ats, reverse=True), "Snapshots not ordered by taken_at DESC"

    print("PASS")