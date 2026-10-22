"""
services.staged.freshness_sla_compliance_api.contract

Self‑testable FastAPI contract for the freshness SLA compliance API.
Mirrors the pattern used in services/_exemplar/contract.py.
"""

from __future__ import annotations

import datetime
import json
import sys
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, APIRouter, HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Real application data layer -------------------------------------------------
from app.db import get_session, Base  # real DB session provider and metadata
from app.models import FreshnessStat  # real model

# -----------------------------------------------------------------------------


router = APIRouter(prefix="/api")


@router.get(
    "/freshness/sla",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def get_freshness_sla(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Return SLA compliance information for each distinct surface."""
    now = datetime.datetime.utcnow()

    # Fetch all freshness stats – the test data contains one row per surface.
    rows: List[FreshnessStat] = db.query(FreshnessStat).all()

    surfaces: List[Dict[str, Any]] = []
    stale_count = 0

    for row in rows:
        # payload is stored as JSON; ensure we have a dict.
        payload: Dict[str, Any] = row.payload if isinstance(row.payload, dict) else {}
        sla_target_seconds: float = float(payload.get("sla_target_seconds", 3600))

        age_seconds = (now - row.computed_at).total_seconds()
        within_sla = age_seconds <= sla_target_seconds

        if not within_sla:
            stale_count += 1

        surfaces.append(
            {
                "surface": row.surface,
                "computed_at": row.computed_at.isoformat(),
                "age_seconds": age_seconds,
                "within_sla": within_sla,
                "sla_target_seconds": sla_target_seconds,
                "payload": payload,
            }
        )

    # Sort by age_seconds descending (oldest first)
    surfaces.sort(key=lambda x: x["age_seconds"], reverse=True)

    return {
        "surfaces": surfaces,
        "stale_count": stale_count,
        "checked_at": now.isoformat(),
    }


# -----------------------------------------------------------------------------


def _build_test_app() -> FastAPI:
    """Create a FastAPI app with the router and a test DB session override."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session  # type: ignore
    return app


# -----------------------------------------------------------------------------


# In‑memory SQLite engine for the self‑test
_test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal = sessionmaker(bind=_test_engine)


def _override_get_session() -> Session:
    """Dependency override returning a fresh session bound to the in‑memory DB."""
    return _TestSessionLocal()


def _seed_test_data() -> None:
    """Populate the in‑memory DB with three FreshnessStat rows."""
    Base.metadata.create_all(bind=_test_engine)

    now = datetime.datetime.utcnow()

    rows = [
        FreshnessStat(
            surface="surface_fresh",
            computed_at=now,
            payload={"sla_target_seconds": 3600},
            compute_seconds=0.0,
        ),
        FreshnessStat(
            surface="surface_stale",
            computed_at=now - datetime.timedelta(seconds=7200),
            payload={"sla_target_seconds": 3600},
            compute_seconds=0.0,
        ),
        FreshnessStat(
            surface="surface_default",
            computed_at=now - datetime.timedelta(seconds=1800),
            payload={},  # no explicit sla_target_seconds → default 3600
            compute_seconds=0.0,
        ),
    ]

    session: Session = _TestSessionLocal()
    session.add_all(rows)
    session.commit()
    session.close()


# -----------------------------------------------------------------------------


def _run_self_test() -> None:
    """Execute the acceptance self‑test."""
    _seed_test_data()
    app = _build_test_app()
    client = TestClient(app)

    resp = client.get("/api/freshness/sla")
    if resp.status_code != 200:
        raise AssertionError(f"Expected 200, got {resp.status_code}")

    data = resp.json()
    surfaces = data.get("surfaces", [])
    if len(surfaces) != 3:
        raise AssertionError(f"Expected 3 surfaces, got {len(surfaces)}")

    stale_flags = [s["within_sla"] for s in surfaces]
    if stale_flags.count(False) != 1:
        raise AssertionError("Expected exactly one surface with within_sla == False")

    if data.get("stale_count") != 1:
        raise AssertionError(f"Expected stale_count == 1, got {data.get('stale_count')}")

    print("PASS")
    sys.exit(0)


# -----------------------------------------------------------------------------


if __name__ == "__main__":
    _run_self_test()