"""
services.staged.perspective_snapshot_fetch.contract
---------------------------------------------------

FastAPI contract for fetching a single perspective snapshot.

The module mirrors the structure of ``services/_exemplar/contract.py`` and
provides a self‑test that can be executed with::

    python -m services.staged.perspective_snapshot_fetch.contract
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

# --------------------------------------------------------------------------- #
# Real application data layer – must be used exactly as the production code.
# --------------------------------------------------------------------------- #
from app.db import Base, get_session  # noqa: F401  (imported for overrides)
from app.models import PerspectiveSnapshot  # noqa: F401

# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class ServerInfo(BaseModel):
    server_id: int
    name: str
    risk_tier: Optional[str] = None


class Membership(BaseModel):
    servers: List[ServerInfo]
    criteria_version: Optional[str] = None


class PerspectiveSnapshotResponse(BaseModel):
    id: int
    perspective_id: int
    taken_at: datetime
    membership: Membership


# --------------------------------------------------------------------------- #
# Router definition
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api")


@router.get(
    "/perspectives/{perspective_id}/snapshots/{snapshot_id}",
    response_model=PerspectiveSnapshotResponse,
    name="perspective_snapshot_fetch",
)
def fetch_snapshot(
    perspective_id: int,
    snapshot_id: int,
    session: Session = Depends(get_session),
) -> PerspectiveSnapshotResponse:
    """
    Retrieve a single ``PerspectiveSnapshot`` belonging to a perspective.
    """
    snapshot = (
        session.query(PerspectiveSnapshot)
        .filter_by(id=snapshot_id, perspective_id=perspective_id)
        .first()
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    # ``membership`` is stored as JSON in the DB; it maps directly onto the
    # Pydantic ``Membership`` model.
    return PerspectiveSnapshotResponse(
        id=snapshot.id,
        perspective_id=snapshot.perspective_id,
        taken_at=snapshot.taken_at,
        membership=Membership(**snapshot.membership),
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run as a script)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import sys

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mirrors the real models.
    # ------------------------------------------------------------------- #
    ENGINE = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=ENGINE, autocommit=False, autoflush=False)

    # Create tables for the models we need.
    Base.metadata.create_all(bind=ENGINE)

    # ------------------------------------------------------------------- #
    # Seed test data.
    # ------------------------------------------------------------------- #
    test_perspective_id = 1
    test_snapshot_id = 42
    test_membership = {
        "servers": [
            {"server_id": 7, "name": "alpha", "risk_tier": "high"},
            {"server_id": 8, "name": "beta", "risk_tier": "low"},
        ],
        "criteria_version": "v1.2",
    }

    with SessionLocal() as db:
        snapshot = PerspectiveSnapshot(
            id=test_snapshot_id,
            perspective_id=test_perspective_id,
            taken_at=datetime.utcnow(),
            membership=test_membership,
        )
        db.add(snapshot)
        db.commit()

    # ------------------------------------------------------------------- #
    # Dependency override so the FastAPI app uses the in‑memory session.
    # ------------------------------------------------------------------- #
    def get_test_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform the request and validate the shape of the response.
    # ------------------------------------------------------------------- #
    url = f"/api/perspectives/{test_perspective_id}/snapshots/{test_snapshot_id}"
    resp = client.get(url)

    try:
        assert resp.status_code == 200, f"unexpected status {resp.status_code}"
        data = resp.json()
        assert data["id"] == test_snapshot_id
        assert data["perspective_id"] == test_perspective_id
        assert isinstance(data["taken_at"], str)
        membership = data["membership"]
        assert isinstance(membership, dict)
        assert membership["criteria_version"] == "v1.2"
        servers = membership["servers"]
        assert isinstance(servers, list) and len(servers) == 2
        assert servers[0]["server_id"] == 7
        assert servers[0]["name"] == "alpha"
        assert servers[0]["risk_tier"] == "high"
        assert servers[1]["server_id"] == 8
        assert servers[1]["name"] == "beta"
        assert servers[1]["risk_tier"] == "low"
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)