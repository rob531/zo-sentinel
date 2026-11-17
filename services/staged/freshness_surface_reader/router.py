"""router.py -- the service's HTTP surface (one concern, one file).

Thin router that injects the real Session via Depends(get_session) and
delegates to the service's logic module.
"""
from __future__ import annotations

import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat

from .logic import read_freshness_surfaces

router = APIRouter(prefix="/api", tags=["freshness_surface_reader"])


class SurfaceItem(BaseModel):
    surface: str
    computed_at: datetime.datetime
    compute_seconds: int
    age_seconds: int
    payload: dict[str, Any]


class FreshnessResponse(BaseModel):
    generated_at: datetime.datetime
    surfaces: list[SurfaceItem]


@router.get(
    "/freshness/surfaces",
    response_model=FreshnessResponse,
    summary="List freshness surface measurements",
)
def get_freshness_surfaces(
    stale_only: bool = Query(False, description="Return only stale surfaces"),
    db: Session = Depends(get_session),
) -> FreshnessResponse:
    """Return the latest freshness statistics per surface.

    If ``stale_only`` is true, only surfaces whose ``age_seconds`` exceeds the
    SLA (``payload['sla_seconds']`` or 3600 seconds default) are returned.
    """
    data = read_freshness_surfaces(db, stale_only=stale_only)
    return FreshnessResponse(**data)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (mirrors the real Postgres schema)
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Create tables for the models we use
    FreshnessStat.metadata.create_all(engine)

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    now = datetime.datetime.utcnow()
    with SessionLocal() as sess:
        sess.add_all(
            [
                FreshnessStat(
                    surface="surface_fresh_1",
                    payload={},
                    compute_seconds=10,
                    computed_at=now,
                ),
                FreshnessStat(
                    surface="surface_fresh_2",
                    payload={},
                    compute_seconds=20,
                    computed_at=now,
                ),
                FreshnessStat(
                    surface="surface_stale",
                    payload={"sla_seconds": 3600},
                    compute_seconds=30,
                    computed_at=now - datetime.timedelta(seconds=7200),
                ),
            ]
        )
        sess.commit()

    # ------------------------------------------------------------------- #
    # Dependency override
    # ------------------------------------------------------------------- #
    def get_test_session() -> Session:
        return SessionLocal()

    # ------------------------------------------------------------------- #
    # FastAPI app wiring
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    app.include_router(router)

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Tests
    # ------------------------------------------------------------------- #
    resp = client.get("/api/freshness/surfaces")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "surfaces" in data, "Missing 'surfaces' key"
    assert len(data["surfaces"]) == 3, f"Expected 3 surfaces, got {len(data['surfaces'])}"

    # stale_only filter
    resp_stale = client.get("/api/freshness/surfaces?stale_only=true")
    assert resp_stale.status_code == 200, f"Unexpected status {resp_stale.status_code}"
    stale_data = resp_stale.json()
    assert len(stale_data["surfaces"]) == 1, f"Expected 1 stale surface, got {len(stale_data['surfaces'])}"
    assert stale_data["surfaces"][0]["surface"] == "surface_stale"

    # age_seconds sanity check
    for surf in data["surfaces"]:
        age = surf.get("age_seconds")
        assert isinstance(age, int) and age >= 0, f"Invalid age_seconds: {age}"

    print("PASS")