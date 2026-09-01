# services/staged/server_perspective/logic.py
from fastapi import APIRouter, Depends, FastAPI, Query, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import List

from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot, Base  # real models

router = APIRouter()


class PerspectiveItem(BaseModel):
    id: int
    name: str
    description: str | None = None
    membership: str | None = None


class PerspectiveResponse(BaseModel):
    server_id: int
    perspectives: List[PerspectiveItem]


@router.get("/api/perspective", response_model=PerspectiveResponse)
def get_perspective(
    server_id: int = Query(..., description="Server identifier"),
    db: Session = Depends(get_session),
):
    """
    Return the latest snapshot for each perspective associated with the given server_id.
    """
    # Filter perspectives by org_id == server_id (the only column linking to a server)
    perspective_subq = (
        select(
            PerspectiveSnapshot.perspective_id,
            func.max(PerspectiveSnapshot.taken_at).label("max_taken"),
        )
        .group_by(PerspectiveSnapshot.perspective_id)
        .subquery()
    )

    stmt = (
        select(
            Perspective.id,
            Perspective.name,
            Perspective.description,
            PerspectiveSnapshot.membership,
        )
        .join(
            PerspectiveSnapshot,
            Perspective.id == PerspectiveSnapshot.perspective_id,
        )
        .join(
            perspective_subq,
            (PerspectiveSnapshot.perspective_id == perspective_subq.c.perspective_id)
            & (PerspectiveSnapshot.taken_at == perspective_subq.c.max_taken),
        )
        .where(Perspective.org_id == server_id)
    )

    rows = db.execute(stmt).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No perspectives found for server")

    items = [
        PerspectiveItem(
            id=row.id,
            name=row.name,
            description=row.description,
            membership=row.membership,
        )
        for row in rows
    ]

    return PerspectiveResponse(server_id=server_id, perspectives=items)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build a temporary FastAPI app with an in‑memory SQLite DB
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker
    from datetime import datetime

    # ------------------------------------------------------------------- #
    # In‑memory DB setup
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables using the real declarative Base from app.models
    Base.metadata.create_all(bind=engine)

    # Dependency override that yields a session bound to the in‑memory engine
    def _override_get_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    with SessionLocal() as db:
        p1 = Perspective(
            id=1,
            name="Perspective One",
            description="First test perspective",
            org_id=42,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            created_by=1,
            facet_filters="{}",
        )
        p2 = Perspective(
            id=2,
            name="Perspective Two",
            description="Second test perspective",
            org_id=42,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            created_by=1,
            facet_filters="{}",
        )
        db.add_all([p1, p2])
        db.flush()  # obtain PKs before snapshots

        snap1 = PerspectiveSnapshot(
            id=1,
            perspective_id=1,
            membership="member_a",
            taken_at=datetime.utcnow(),
        )
        snap2 = PerspectiveSnapshot(
            id=2,
            perspective_id=2,
            membership="member_b",
            taken_at=datetime.utcnow(),
        )
        # Add a stale snapshot for perspective 1 to ensure latest is selected
        snap_old = PerspectiveSnapshot(
            id=3,
            perspective_id=1,
            membership="old_member",
            taken_at=datetime(2000, 1, 1),
        )
        db.add_all([snap1, snap2, snap_old])
        db.commit()

    # ------------------------------------------------------------------- #
    # FastAPI app wiring
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute test request
    # ------------------------------------------------------------------- #
    response = client.get("/api/perspective", params={"server_id": 42})
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()
    assert data["server_id"] == 42
    assert len(data["perspectives"]) == 2

    # Verify each perspective payload
    expected = {
        1: {"name": "Perspective One", "description": "First test perspective", "membership": "member_a"},
        2: {"name": "Perspective Two", "description": "Second test perspective", "membership": "member_b"},
    }
    for item in data["perspectives"]:
        pid = item["id"]
        assert pid in expected, f"Unexpected perspective id {pid}"
        exp = expected[pid]
        assert item["name"] == exp["name"]
        assert item["description"] == exp["description"]
        assert item["membership"] == exp["membership"]

    print("PASS")