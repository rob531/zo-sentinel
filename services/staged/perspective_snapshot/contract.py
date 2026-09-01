"""
services.staged.perspective_snapshot.contract
"""

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Real data layer imports (must remain unchanged for production)
from app.db import get_session
from app.models import (
    Base,
    Perspective,
    McpServerRegistry,
    PerspectiveSnapshot,
)

# Service router import
from services.staged.perspective_snapshot.router import router as snapshot_router

app = FastAPI()
app.include_router(snapshot_router, prefix="/api")


if __name__ == "__main__":
    # ----------------------------------------------------------------------
    # In‑memory SQLite setup for self‑test (dependency override)
    # ----------------------------------------------------------------------
    SQLITE_URL = "sqlite:///:memory:"
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Create all tables defined in the real models
    Base.metadata.create_all(bind=engine)

    # Dependency override to use the in‑memory session
    def _override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _override_get_session

    # ----------------------------------------------------------------------
    # Seed minimal data required for the acceptance test
    # ----------------------------------------------------------------------
    with SessionLocal() as db:
        # Perspective (id=1)
        perspective = Perspective(id=1, name="test_perspective")
        db.add(perspective)

        # Three server registry entries; only one will match the default filter
        servers = [
            McpServerRegistry(server_id=1, perspective_id=1, facet="allowed"),
            McpServerRegistry(server_id=2, perspective_id=1, facet="blocked"),
            McpServerRegistry(server_id=3, perspective_id=1, facet="blocked"),
        ]
        db.add_all(servers)
        db.commit()

    # ----------------------------------------------------------------------
    # Run acceptance test
    # ----------------------------------------------------------------------
    client = TestClient(app)

    # POST snapshot – no body required for default behaviour
    response = client.post("/api/perspective/1/snapshot")
    assert response.status_code == 201, f"Unexpected status {response.status_code}"
    payload = response.json()
    assert "snapshot_id" in payload, "Missing snapshot_id in response"
    assert "membership_count" in payload, "Missing membership_count in response"
    assert payload["membership_count"] == 1, "Expected exactly one server in membership"

    # Verify the snapshot record was created and contains the expected server_id
    with SessionLocal() as db:
        snap = db.query(PerspectiveSnapshot).filter_by(id=payload["snapshot_id"]).one_or_none()
        assert snap is not None, "Snapshot record not found in DB"
        # Assuming the snapshot stores a JSON list of server IDs in a column named `membership`
        membership = getattr(snap, "membership", None)
        assert membership is not None, "Snapshot missing membership data"
        # membership may be stored as JSON string or list; handle both
        if isinstance(membership, str):
            import json

            membership = json.loads(membership)
        assert isinstance(membership, (list, tuple)), "Membership is not a list"
        assert 1 in membership, "Expected server_id 1 in membership"

    print("PASS")