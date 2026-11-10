"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.db import Base, get_session
from app.models import FreshnessStat

from .logic import get_freshness_status

router = APIRouter(prefix="/api", tags=["registry_freshness_status"])


class FreshnessSurfaceOut(BaseModel):
    surface: str
    computed_at: datetime
    age_seconds: int
    compute_seconds: int
    stale: bool


class FreshnessStatusOut(BaseModel):
    generated_at: datetime
    surfaces: list[FreshnessSurfaceOut]
    stale_count: int


@router.get("/freshness/surfaces", response_model=FreshnessStatusOut)
def get_freshness_surfaces(db: Session = Depends(get_session)) -> FreshnessStatusOut:
    """Thin wrapper delegating to the service logic."""
    return get_freshness_status(db)


# --------------------------------------------------------------------------- #
# __main__ self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build an in‑memory SQLite engine that mimics the real DB.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Create tables.
    Base.metadata.create_all(bind=engine)

    # Dependency override that yields a session bound to the in‑memory engine.
    def _override_get_session() -> Session:  # pragma: no cover
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed test data.
    now = datetime.utcnow()
    fresh = FreshnessStat(
        surface="fresh_surface",
        payload={"info": "fresh"},
        compute_seconds=5,
        computed_at=now,
    )
    stale = FreshnessStat(
        surface="stale_surface",
        payload={"info": "stale"},
        compute_seconds=7,
        computed_at=now - timedelta(hours=2),
    )
    with SessionLocal() as db:
        db.add_all([fresh, stale])
        db.commit()

    # Build FastAPI app with the router and dependency override.
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    # Perform request and validate contract.
    resp = client.get("/api/freshness/surfaces")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert isinstance(data, dict), "Response is not a dict"
    assert "generated_at" in data
    assert "surfaces" in data and isinstance(data["surfaces"], list)
    assert len(data["surfaces"]) == 2, f"Expected 2 surfaces, got {len(data['surfaces'])}"
    assert "stale_count" in data and data["stale_count"] == 1, "Stale count mismatch"

    # Verify stale flag per surface.
    for surf in data["surfaces"]:
        if surf["surface"] == "fresh_surface":
            assert surf["stale"] is False, "Fresh surface marked stale"
        elif surf["surface"] == "stale_surface":
            assert surf["stale"] is True, "Stale surface not marked stale"

    print("PASS")