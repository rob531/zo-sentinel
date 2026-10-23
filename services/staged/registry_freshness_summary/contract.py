"""
services/staged/registry_freshness_summary/contract.py

FastAPI contract for the Registry Freshness Summary service.

Provides:
    GET /api/registry/freshness
        Returns a summary of server scan freshness.

The module mirrors `services/_exemplar/contract.py` and uses the real
application data layer (`app.db` and `app.models`). The self‑test at the
bottom runs an in‑memory SQLite database with a static pool, seeds test
data, and validates the endpoint.
"""

from __future__ import annotations

import datetime
import sys
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.db import Base, get_session
from app.models import McpServerRegistry
from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------


class StaleServer(BaseModel):
    server_id: str = Field(..., description="Unique identifier of the server")
    name: str = Field(..., description="Human readable server name")
    last_scanned_iso: Optional[str] = Field(
        None, description="ISO‑8601 timestamp of the last scan"
    )
    days_since_scan: Optional[int] = Field(
        None, description="Number of days since the last scan"
    )


class FreshnessSummary(BaseModel):
    total_servers: int = Field(..., description="Total number of servers")
    never_scanned: int = Field(..., description="Servers that have never been scanned")
    stale: int = Field(..., description="Servers considered stale")
    stale_threshold_days: int = Field(..., description="Staleness threshold in days")
    stale_servers: List[StaleServer] = Field(
        default_factory=list, description="Details of stale servers"
    )


# ----------------------------------------------------------------------
# Router definition
# ----------------------------------------------------------------------


router = APIRouter(prefix="/api", tags=["registry_freshness_summary"])


@router.get(
    "/registry/freshness",
    response_model=FreshnessSummary,
    summary="Get a freshness summary of the server registry",
)
def get_registry_freshness_summary(
    session: Session = Depends(get_session),
    stale_threshold_days: int = 30,
) -> FreshnessSummary:
    """
    Compute a freshness summary for all servers in `McpServerRegistry`.

    * **Never scanned** – `last_scanned` is NULL.
    * **Stale** – `last_scanned` is older than `stale_threshold_days`.
    * **Fresh** – all other servers (implicitly total - never_scanned - stale).

    Returns a `FreshnessSummary` model.
    """
    now = datetime.datetime.utcnow()

    # Retrieve all registry rows
    rows: List[McpServerRegistry] = session.query(McpServerRegistry).all()

    total = len(rows)
    never_scanned = 0
    stale = 0
    stale_servers: List[StaleServer] = []

    threshold_delta = datetime.timedelta(days=stale_threshold_days)

    for row in rows:
        # `last_scanned` may be None (never scanned)
        if not getattr(row, "last_scanned", None):
            never_scanned += 1
            continue

        last_scanned: datetime.datetime = row.last_scanned  # type: ignore
        age = now - last_scanned

        if age > threshold_delta:
            stale += 1
            stale_servers.append(
                StaleServer(
                    server_id=str(getattr(row, "server_id")),
                    name=getattr(row, "name"),
                    last_scanned_iso=last_scanned.isoformat(),
                    days_since_scan=age.days,
                )
            )

    return FreshnessSummary(
        total_servers=total,
        never_scanned=never_scanned,
        stale=stale,
        stale_threshold_days=stale_threshold_days,
        stale_servers=stale_servers,
    )


# ----------------------------------------------------------------------
# Include router in a FastAPI app (used by the main application)
# ----------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


# ----------------------------------------------------------------------
# Self‑test (run with `python -m services.staged.registry_freshness_summary.contract`)
# ----------------------------------------------------------------------


if __name__ == "__main__":
    # Build an in‑memory SQLite engine with a static pool (so the same
    # connection is reused across sessions, matching the production
    # session pattern).
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    ENGINE = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=ENGINE)

    # Create tables for the test DB.
    Base.metadata.create_all(bind=ENGINE)

    # Seed test data.
    now = datetime.datetime.utcnow()
    stale_threshold = 30
    stale_date = now - datetime.timedelta(days=stale_threshold + 1)  # 31 days ago
    fresh_date = now - datetime.timedelta(days=5)  # recent

    test_rows = [
        # Never scanned (last_scanned = None)
        McpServerRegistry(server_id="srv-1", name="Never 1", last_scanned=None),
        McpServerRegistry(server_id="srv-2", name="Never 2", last_scanned=None),
        McpServerRegistry(server_id="srv-3", name="Never 3", last_scanned=None),
        # Stale
        McpServerRegistry(server_id="srv-4", name="Stale", last_scanned=stale_date),
        # Fresh
        McpServerRegistry(server_id="srv-5", name="Fresh", last_scanned=fresh_date),
    ]

    with SessionLocal() as db:
        db.add_all(test_rows)
        db.commit()

    # Build FastAPI app with dependency override.
    app = create_app()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    client = TestClient(app)

    resp = client.get("/api/registry/freshness")
    if resp.status_code != 200:
        print(f"FAIL – unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    try:
        assert data["total_servers"] == 5
        assert data["never_scanned"] == 3
        assert data["stale"] == 1
        assert data["stale_threshold_days"] == stale_threshold
        assert len(data["stale_servers"]) == 1
        stale_srv = data["stale_servers"][0]
        assert stale_srv["server_id"] == "srv-4"
        assert stale_srv["name"] == "Stale"
        # days_since_scan should be at least 31
        assert stale_srv["days_since_scan"] >= 31
    except AssertionError:
        print("FAIL – response content mismatch", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)