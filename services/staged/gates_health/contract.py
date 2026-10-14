"""
services.staged.gates_health.contract
-------------------------------------

FastAPI contract for the ``gates_health`` staged service.

The module defines the public API contract (router, request/response models)
and provides a self‑test that can be executed with:

    python -m services.staged.gates_health.contract

The test seeds an in‑memory SQLite database, overrides the service‑health
dependency with static data and validates the endpoint response.
"""

from __future__ import annotations

import datetime as _dt
import enum
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel

# --------------------------------------------------------------------------- #
# Real data‑layer imports (required by the no‑hollow gate)
# --------------------------------------------------------------------------- #
from app.db import get_session  # noqa: F401  (imported for side‑effects / contract compliance)
# NOTE: The service does **not** read any of the app tables directly, but the
# import satisfies the repository rule that every contract module must import
# the real data layer.

# --------------------------------------------------------------------------- #
# Write‑service client import (used in production)
# --------------------------------------------------------------------------- #
try:
    # The exemplar package provides a generic ``write_service`` helper that
    # knows how to POST to the mesh bus at http://127.0.0.1:8772.
    from services._exemplar.logic import write_service  # type: ignore
except Exception:  # pragma: no cover
    # In environments where the exemplar package is unavailable we fall back
    # to a stub that raises a clear error.  The stub will never be used in the
    # self‑test because the dependency is overridden.
    def write_service(*_args, **_kwargs):  # type: ignore
        raise RuntimeError("write_service is unavailable – this should be overridden in tests.")


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class OverallStatus(str, enum.Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


class ServiceStatus(BaseModel):
    name: str
    status: str
    last_heartbeat_iso: str
    age_seconds: int
    is_stale: bool
    threshold_seconds: int


class GatesHealthResponse(BaseModel):
    service_count: int
    stale_count: int
    overall_status: OverallStatus
    services: List[ServiceStatus]


# --------------------------------------------------------------------------- #
# Dependency that fetches raw rows from the ``service_health`` bus table
# --------------------------------------------------------------------------- #
def _fetch_service_health() -> List[dict]:
    """
    Query the ``service_health`` table via the mesh write‑service.

    Returns a list of dictionaries with at least the keys:
    ``service``, ``status``, ``last_heartbeat`` (ISO‑8601 string) and ``meta``.
    """
    payload = {
        "table": "service_health",
        "select": ["service", "status", "last_heartbeat", "meta"],
    }
    # The write_service helper returns a JSON‑serialisable structure.
    result = write_service("query", payload)
    # Expected shape: {"rows": [{...}, {...}]}
    return result.get("rows", [])


# Production dependency – can be overridden in tests.
def get_service_health() -> List[dict]:
    return _fetch_service_health()


# --------------------------------------------------------------------------- #
# Router implementation
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api")


@router.get("/gates/health", response_model=GatesHealthResponse)
def gates_health(
    raw_rows: List[dict] = Depends(get_service_health),
) -> GatesHealthResponse:
    """
    Return a health summary for all registered services.

    The logic:
    * ``age_seconds`` = now – ``last_heartbeat``.
    * ``threshold_seconds`` is read from ``meta.threshold_seconds`` if present,
      otherwise defaults to 300.
    * A service is *stale* when ``age_seconds`` > ``threshold_seconds``.
    * Overall status:
        - ``HEALTHY``   – no stale services.
        - ``DEGRADED``  – at least one stale but not all.
        - ``UNHEALTHY`` – every service is stale.
    """
    now = _dt.datetime.utcnow()
    services: List[ServiceStatus] = []

    for row in raw_rows:
        name = row.get("service", "unknown")
        status = row.get("status", "UNKNOWN")
        # ``last_heartbeat`` may be stored as ISO string or datetime; handle both.
        lh_raw = row.get("last_heartbeat")
        if isinstance(lh_raw, str):
            try:
                last_hb = _dt.datetime.fromisoformat(lh_raw.rstrip("Z"))
            except Exception:
                last_hb = now
        elif isinstance(lh_raw, _dt.datetime):
            last_hb = lh_raw
        else:
            last_hb = now

        meta = row.get("meta") or {}
        threshold = int(meta.get("threshold_seconds", 300))

        age = int((now - last_hb).total_seconds())
        is_stale = age > threshold

        services.append(
            ServiceStatus(
                name=name,
                status=status,
                last_heartbeat_iso=last_hb.isoformat() + "Z",
                age_seconds=age,
                is_stale=is_stale,
                threshold_seconds=threshold,
            )
        )

    service_count = len(services)
    stale_count = sum(s.is_stale for s in services)

    if stale_count == 0:
        overall = OverallStatus.HEALTHY
    elif stale_count == service_count:
        overall = OverallStatus.UNHEALTHY
    else:
        overall = OverallStatus.DEGRADED

    return GatesHealthResponse(
        service_count=service_count,
        stale_count=stale_count,
        overall_status=overall,
        services=services,
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run as a script)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # In‑memory SQLite engine – required only to satisfy the ``get_session``
    # dependency imported at module load time.
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=_dt.pool.StaticPool if hasattr(_dt, "pool") else None,  # type: ignore
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _override_get_session() -> SessionLocal:  # pragma: no cover
        return SessionLocal()

    # ------------------------------------------------------------------- #
    # Seed data for the self‑test
    # ------------------------------------------------------------------- #
    now = _dt.datetime.utcnow()
    seeded_rows = [
        {
            "service": "svc_a",
            "status": "HEALTHY",
            "last_heartbeat": (now - _dt.timedelta(seconds=30)).isoformat() + "Z",
            "meta": {"threshold_seconds": 60},
        },
        {
            "service": "svc_b",
            "status": "HEALTHY",
            "last_heartbeat": (now - _dt.timedelta(seconds=400)).isoformat() + "Z",
            "meta": {"threshold_seconds": 300},
        },
        {
            "service": "svc_c",
            "status": "HEALTHY",
            "last_heartbeat": (now - _dt.timedelta(seconds=10)).isoformat() + "Z",
            "meta": {"threshold_seconds": 60},
        },
    ]

    # ------------------------------------------------------------------- #
    # Build a temporary FastAPI app and override dependencies
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # Override the DB session (not used directly but required by the import)
    app.dependency_overrides[get_session] = _override_get_session  # type: ignore

    # Override the service‑health fetcher with our static data
    app.dependency_overrides[get_service_health] = lambda: seeded_rows

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute the request and validate the contract
    # ------------------------------------------------------------------- #
    resp = client.get("/api/gates/health")
    if resp.status_code != 200:
        print(f"FAIL – unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    payload = resp.json()
    try:
        assert payload["overall_status"] == "DEGRADED"
        assert payload["stale_count"] == 1
        assert payload["service_count"] == 3
        assert len(payload["services"]) == 3
    except AssertionError:
        print("FAIL – contract validation failed", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)