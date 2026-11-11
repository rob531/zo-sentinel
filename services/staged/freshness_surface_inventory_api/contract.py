"""Freshness Surface Inventory API contract.

Provides a GET endpoint at /api/freshness/surfaces that returns the current
freshness statistics for each surface.

Running this module directly executes a self‑test that seeds an in‑memory
SQLite database, invokes the endpoint via TestClient and validates the
response.  On success it prints ``PASS`` and exits with status 0.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import FreshnessStat
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

router = APIRouter(prefix="/api")


class SurfaceInfo(BaseModel):
    surface: str
    computed_at: datetime
    compute_seconds: float
    age_seconds: float
    payload: dict
    is_stale: bool = Field(False, description="True if age exceeds max_age_seconds")


class FreshnessResponse(BaseModel):
    surfaces: List[SurfaceInfo]
    as_of: datetime
    stale_count: int


@router.get(
    "/freshness/surfaces",
    response_model=FreshnessResponse,
    summary="List freshness statistics for all surfaces",
)
def list_freshness_surfaces(
    max_age_seconds: int = Query(900, ge=0, description="Maximum age before a surface is stale"),
    session: Session = Depends(get_session),
) -> FreshnessResponse:
    """Return freshness information for every surface.

    * ``age_seconds`` is computed against the current UTC time.
    * ``is_stale`` is true when ``age_seconds`` exceeds ``max_age_seconds``.
    * ``stale_count`` is the number of surfaces flagged as stale.
    """
    now = datetime.utcnow()
    rows = session.query(FreshnessStat).all()

    surfaces: List[SurfaceInfo] = []
    stale_cnt = 0

    for row in rows:
        age = (now - row.computed_at).total_seconds()
        stale = age > max_age_seconds
        if stale:
            stale_cnt += 1
        surfaces.append(
            SurfaceInfo(
                surface=row.surface,
                computed_at=row.computed_at,
                compute_seconds=row.compute_seconds,
                age_seconds=age,
                payload=row.payload,
                is_stale=stale,
            )
        )

    return FreshnessResponse(surfaces=surfaces, as_of=now, stale_count=stale_cnt)


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    # --------------------------------------------------------------------- #
    # Build a temporary FastAPI app with an in‑memory SQLite DB
    # --------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # Create a SQLite engine that lives only in memory and is shared across
    # connections (StaticPool) so that the same DB is visible to all sessions.
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=test_engine)

    # Create the FreshnessStat table in the temporary DB.
    FreshnessStat.__table__.create(bind=test_engine)

    # Seed the DB with three rows:
    #   1. Fresh row (computed now)
    #   2. Row with compute_seconds = 12.5 (still fresh)
    #   3. Stale row (computed 2 hours ago)
    now_utc = datetime.utcnow()
    seed_data = [
        FreshnessStat(
            surface="surface_fresh",
            computed_at=now_utc,
            compute_seconds=5.0,
            payload={"info": "fresh"},
        ),
        FreshnessStat(
            surface="surface_compute_12_5",
            computed_at=now_utc - timedelta(seconds=100),
            compute_seconds=12.5,
            payload={"info": "mid"},
        ),
        FreshnessStat(
            surface="surface_stale",
            computed_at=now_utc - timedelta(hours=2),
            compute_seconds=8.0,
            payload={"info": "stale"},
        ),
    ]

    # Insert seed rows.
    with TestSessionLocal() as sess:
        sess.add_all(seed_data)
        sess.commit()

    # Dependency override so that the endpoint uses our test session.
    def override_get_session() -> Session:  # type: ignore[override]
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # --------------------------------------------------------------------- #
    # Execute the acceptance test via TestClient
    # --------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get("/api/freshness/surfaces")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # Basic shape checks
    assert "surfaces" in data and isinstance(data["surfaces"], list), "Missing surfaces list"
    assert len(data["surfaces"]) == 3, f"Expected 3 surfaces, got {len(data['surfaces'])}"
    assert "stale_count" in data and isinstance(data["stale_count"], int), "Missing stale_count"

    # Find each seeded surface
    fresh = next(s for s in data["surfaces"] if s["surface"] == "surface_fresh")
    compute_12_5 = next(s for s in data["surfaces"] if s["surface"] == "surface_compute_12_5")
    stale = next(s for s in data["surfaces"] if s["surface"] == "surface_stale")

    # Fresh surface must not be stale
    assert not fresh["is_stale"], "Fresh surface incorrectly flagged as stale"

    # compute_seconds must be echoed correctly
    assert compute_12_5["compute_seconds"] == 12.5, "compute_seconds not echoed"

    # Stale surface must be flagged and counted
    assert stale["is_stale"], "Stale surface not flagged"
    assert data["stale_count"] == 1, f"Expected stale_count 1, got {data['stale_count']}"

    print("PASS")
    sys.exit(0)