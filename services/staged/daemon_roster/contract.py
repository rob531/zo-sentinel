"""
services/staged/daemon_roster/contract.py

FastAPI contract for the daemon roster health endpoint.
Mirrors the pattern used in services/_exemplar/contract.py.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class DaemonInfo(BaseModel):
    """Information about a single daemon."""

    name: str
    status: str
    last_heartbeat: _dt.datetime
    meta: Dict[str, Any] = Field(default_factory=dict)


class DaemonRosterResponse(BaseModel):
    """Response payload for the daemon roster endpoint."""

    daemons: List[DaemonInfo]
    stale_count: int
    healthy_count: int


# --------------------------------------------------------------------------- #
# Dependency that talks to the write‑service bus (service_health table)
# --------------------------------------------------------------------------- #


def fetch_service_health() -> List[Dict[str, Any]]:
    """
    Query the write‑service for rows in the ``service_health`` table.

    Returns
    -------
    List[Dict[str, Any]]
        A list of dictionaries, each representing a row with the keys
        ``name``, ``status``, ``last_heartbeat`` and ``meta``.
    """
    sql = """
        SELECT
            name,
            status,
            last_heartbeat,
            meta
        FROM service_health
    """
    response = httpx.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql},
        timeout=10.0,
    )
    response.raise_for_status()
    payload = response.json()
    # The write‑service returns rows under the ``rows`` key.
    return payload.get("rows", [])


# --------------------------------------------------------------------------- #
# Router definition
# --------------------------------------------------------------------------- #

router = APIRouter(prefix="/api")


@router.get(
    "/health/daemons",
    response_model=DaemonRosterResponse,
    summary="Return health information for all daemons",
)
def get_daemon_roster(
    rows: List[Dict[str, Any]] = Depends(fetch_service_health),
) -> DaemonRosterResponse:
    """
    Assemble the daemon roster response from the raw ``service_health`` rows.
    """
    daemons = [DaemonInfo(**row) for row in rows]

    stale = sum(1 for d in daemons if d.status.lower() == "stale")
    healthy = sum(1 for d in daemons if d.status.lower() == "healthy")

    return DaemonRosterResponse(
        daemons=daemons,
        stale_count=stale,
        healthy_count=healthy,
    )


# --------------------------------------------------------------------------- #
# Self‑test (runnable with ``python -m services.staged.daemon_roster.contract``)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":  # pragma: no cover
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # Seed data for the self‑test
    # ------------------------------------------------------------------- #
    _seed_rows = [
        {
            "name": "daemon_a",
            "status": "healthy",
            "last_heartbeat": _dt.datetime.utcnow().isoformat(),
            "meta": {"version": "1.0"},
        },
        {
            "name": "daemon_b",
            "status": "stale",
            "last_heartbeat": (_dt.datetime.utcnow() - _dt.timedelta(minutes=10)).isoformat(),
            "meta": {"version": "1.1"},
        },
        {
            "name": "daemon_c",
            "status": "healthy",
            "last_heartbeat": _dt.datetime.utcnow().isoformat(),
            "meta": {"version": "2.0"},
        },
        {
            "name": "daemon_d",
            "status": "error",
            "last_heartbeat": (_dt.datetime.utcnow() - _dt.timedelta(hours=1)).isoformat(),
            "meta": {"version": "2.1"},
        },
        {
            "name": "daemon_e",
            "status": "stale",
            "last_heartbeat": (_dt.datetime.utcnow() - _dt.timedelta(days=1)).isoformat(),
            "meta": {"version": "3.0"},
        },
    ]

    # ------------------------------------------------------------------- #
    # Build a temporary FastAPI app and override the fetch dependency
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # Override the fetch_service_health dependency with the seeded data
    app.dependency_overrides[fetch_service_health] = lambda: _seed_rows

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute the request and perform assertions
    # ------------------------------------------------------------------- #
    response = client.get("/api/health/daemons")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()

    assert isinstance(payload.get("daemons"), list) and len(payload["daemons"]) > 0, "Daemons list empty"
    assert payload.get("stale_count", -1) >= 0, "Stale count negative"
    # healthy_count is also expected to be non‑negative
    assert payload.get("healthy_count", -1) >= 0, "Healthy count negative"

    print("PASS")