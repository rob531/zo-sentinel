"""
breaker_action_investigate_sentinel_directive_generator.py

Breaker action “investigate” for the sentinel_directive_generator service.
It inspects the service’s last heartbeat and reports whether it is stale
(relative to the 7500 s threshold) so that an on‑call operator can decide
to reset or accept the service state.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

# Application data layer – must be imported exactly as required.
from app.db import get_session
from app.models import mcp_server_registry  # type: ignore

router = APIRouter()


def _fetch_registry(session: Session) -> Any:
    """Return the registry row for the sentinel_directive_generator service."""
    return (
        session.query(mcp_server_registry)
        .filter(mcp_server_registry.service_name == "sentinel_directive_generator")
        .first()
    )


def _compute_staleness(last_heartbeat: datetime.datetime) -> float:
    """Seconds elapsed since ``last_heartbeat``."""
    now = datetime.datetime.utcnow()
    return (now - last_heartbeat).total_seconds()


@router.get("/investigate", response_model=Dict[str, Any])
def investigate(session: Session = Depends(get_session)) -> Dict[str, Any]:
    """
    Investigate the sentinel_directive_generator service.

    Returns a report containing:
        - service name
        - status (``stale`` or ``healthy``)
        - stale_seconds (float)
        - recommendation (``reset`` or ``accept``)
    """
    record = _fetch_registry(session)
    if record is None:
        return {
            "service": "sentinel_directive_generator",
            "status": "missing",
            "stale_seconds": None,
            "recommendation": "manual_inspection",
        }

    # ``last_heartbeat`` may be stored under different column names; fall back to ``updated_at``.
    last_hb = getattr(record, "last_heartbeat", None) or getattr(record, "updated_at", None)
    if last_hb is None:
        return {
            "service": "sentinel_directive_generator",
            "status": "unknown",
            "stale_seconds": None,
            "recommendation": "manual_inspection",
        }

    stale_seconds = _compute_staleness(last_hb)
    threshold = 7500.0
    status = "stale" if stale_seconds > threshold else "healthy"
    recommendation = "reset" if status == "stale" else "accept"

    return {
        "service": "sentinel_directive_generator",
        "status": status,
        "stale_seconds": stale_seconds,
        "recommendation": recommendation,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite DB and bind the app models to it.
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    from app.models import Base  # type: ignore

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    # Override the FastAPI dependency to use the in‑memory session.
    def get_test_session() -> Session:
        return SessionLocal()

    # Insert a fake registry entry that is deliberately stale (>340 h).
    with SessionLocal() as db:
        stale_time = datetime.datetime.utcnow() - datetime.timedelta(hours=340)
        db.add(
            mcp_server_registry(
                service_name="sentinel_directive_generator",
                last_heartbeat=stale_time,
            )
        )
        db.commit()

    # Patch the dependency for a direct call.
    from fastapi import Depends

    def test_dep() -> Session:
        return SessionLocal()

    # Directly invoke the endpoint logic.
    report = investigate(session=SessionLocal())
    expected = {"status": "stale", "recommendation": "reset"}

    if report.get("status") == expected["status"] and report.get(
        "recommendation"
    ) == expected["recommendation"]:
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)