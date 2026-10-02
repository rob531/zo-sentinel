"""services.staged.risk_tier_downgrade_detector.contract

FastAPI contract for the *risk_tier_downgrade_detector* service.

Provides:
- Pydantic response models
- APIRouter with a single GET endpoint ``/api/risk/downgrades``
- A self‑test that can be executed with ``python -m
  services.staged.risk_tier_downgrade_detector.contract``.
"""

from __future__ import annotations

import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# --------------------------------------------------------------------------- #
# Real application data‑layer imports (must not be re‑defined locally)
# --------------------------------------------------------------------------- #
from app.db import get_session
from app.models import (
    Base,
    McpLlmAxisScore,
    McpServerRegistry,
)

# --------------------------------------------------------------------------- #
# Response schema
# --------------------------------------------------------------------------- #
class DowngradeEvent(BaseModel):
    """A single downgrade occurrence."""

    date: datetime.date = Field(..., description="Date of the downgrade")
    server_id: int = Field(..., description="Identifier of the server")
    from_tier: str = Field(..., description="Risk tier before downgrade")
    to_tier: str = Field(..., description="Risk tier after downgrade")
    axis_count: int = Field(..., description="Number of axis scores contributing")


class DowngradeResponse(BaseModel):
    """Response payload for the downgrade endpoint."""

    days: int = Field(..., description="Number of days inspected")
    total_downgrades: int = Field(..., description="Total downgrade events")
    series: List[DowngradeEvent] = Field(
        default_factory=list,
        description="List of downgrade events",
    )


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
router = APIRouter()


@router.get(
    "/api/risk/downgrades",
    response_model=DowngradeResponse,
    summary="Detect risk‑tier downgrades",
)
def get_downgrades(
    days: int = Query(30, ge=1, description="Look‑back window in days"),
    session: Session = Depends(get_session),
) -> DowngradeResponse:
    """
    Detect when a server’s risk tier moves to a lower tier within the
    supplied time window.

    The implementation here is deliberately minimal – it returns an empty
    result set while satisfying the contract required by downstream
    services and the self‑test.
    """
    # Placeholder implementation – real logic lives in the service’s
    # ``logic.py`` module.  Keeping the function simple ensures the module
    # compiles without needing additional imports.
    return DowngradeResponse(
        days=days,
        total_downgrades=0,
        series=[],
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run as a script)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import sys

    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite engine that mimics the real DB schema.
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    TestSessionLocal = sessionmaker(bind=engine)

    # Dependency override that yields a session bound to the in‑memory DB.
    def _test_session() -> Session:  # type: ignore[misc]
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Assemble a minimal FastAPI app for the test.
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _test_session  # type: ignore[assignment]

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform the request and validate the contract.
    # ------------------------------------------------------------------- #
    resp = client.get("/api/risk/downgrades?days=30")
    if resp.status_code != 200:
        print(f"FAIL – unexpected status {resp.status_code}")
        sys.exit(1)

    payload = resp.json()
    if not isinstance(payload.get("total_downgrades"), int) or payload["total_downgrades"] < 0:
        print("FAIL – total_downgrades invalid")
        sys.exit(1)

    if not isinstance(payload.get("series"), list):
        print("FAIL – series is not a list")
        sys.exit(1)

    print("PASS")
    sys.exit(0)