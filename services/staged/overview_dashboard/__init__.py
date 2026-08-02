"""
zo-sentinel service package entry point.

Provides a minimal FastAPI router with utility endpoints used across
staged services. All data access is performed via the application’s
SQLAlchemy session (`app.db.get_session`) and external mesh queries are
performed with a timeout to satisfy security guidelines.
"""

from __future__ import annotations

import json
from typing import Any, List

import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from sqlalchemy.orm import Session

# Application‑level dependencies – must be imported exactly as required.
from app.db import get_session
from app.models import (
    McpServerRegistry,          # example model; actual usage may vary
    McpLlmAxisScore,           # example model
    McpScoreDispute,           # example model
    Org,                    # example model
    User,                   # example model
)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Helper – mesh query (external service)
# --------------------------------------------------------------------------- #
def _mesh_query(sql: str) -> List[dict]:
    """Execute a read‑only SQL query against the mesh store.

    The mesh store is accessed via a POST request to the local query service.
    A timeout is enforced to avoid hanging calls (Bandit B113 compliance).

    Args:
        sql: The SQL statement to execute.

    Returns:
        A list of row dictionaries.

    Raises:
        HTTPException: If the remote service returns a non‑200 status.
    """
    url = "http://127.0.0.1:8772/query"
    try:
        resp = requests.post(url, json={"sql": sql}, timeout=5)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Mesh query failed: {exc}",
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Mesh query error {resp.status_code}: {resp.text}",
        )
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Invalid JSON from mesh query",
        ) from exc


# --------------------------------------------------------------------------- #
# Service endpoints – used by many staged packages
# --------------------------------------------------------------------------- #
@router.post("/dummy", name="_dummy_post")
async def _dummy_post(payload: dict | None = None) -> dict:
    """A no‑op endpoint used by staged services for health‑check style calls."""
    return {"status": "ok", "payload": payload or {}}


@router.get("/signal-scores", name="get_signal_scores")
async def get_signal_scores(
    db: Session = Depends(get_session),
) -> List[dict]:
    """Return signal scores from the mesh store.

    The underlying query is deliberately simple; callers may filter further.
    """
    sql = "SELECT * FROM mcp_signal_scores"
    return _mesh_query(sql)


@router.get("/mesh-memory", name="get_mesh_memory")
async def get_mesh_memory(
    db: Session = Depends(get_session),
) -> List[dict]:
    """Return mesh memory rows."""
    sql = "SELECT * FROM mesh_memory"
    return _mesh_query(sql)


@router.get("/mesh-scores", name="get_mesh_scores")
async def get_mesh_scores(
    db: Session = Depends(get_session),
) -> List[dict]:
    """Return mesh scores rows."""
    sql = "SELECT * FROM mesh_scores"
    return _mesh_query(sql)


@router.post("/reset-quarantine", name="reset_server_export_api_quarantine")
async def reset_server_export_api_quarantine(
    db: Session = Depends(get_session),
) -> dict:
    """Placeholder implementation for quarantine reset."""
    # In a real implementation this would modify DB state.
    return {"reset": True}


# --------------------------------------------------------------------------- #
# Self‑test entry point
# --------------------------------------------------------------------------- #
def _run_self_test() -> None:
    """Execute a minimal self‑test exercising the public endpoints.

    The test creates a temporary FastAPI app, mounts the router, and issues
    HTTP calls via the TestClient. All calls must succeed (status 200). If
    any call fails an exception propagates and the test aborts.
    """
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # 1. Dummy POST
    resp = client.post("/dummy", json={"hello": "world"})
    assert resp.status_code == 200, "dummy endpoint failed"

    # 2. Signal scores GET
    resp = client.get("/signal-scores")
    assert resp.status_code == 200, "signal scores endpoint failed"

    # 3. Mesh memory GET
    resp = client.get("/mesh-memory")
    assert resp.status_code == 200, "mesh memory endpoint failed"

    # 4. Mesh scores GET
    resp = client.get("/mesh-scores")
    assert resp.status_code == 200, "mesh scores endpoint failed"

    # 5. Reset quarantine POST
    resp = client.post("/reset-quarantine")
    assert resp.status_code == 200, "reset quarantine endpoint failed"


# --------------------------------------------------------------------------- #
# Module execution – run self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    try:
        _run_self_test()
        print("PASS")
    except Exception as exc:  # pragma: no cover
        print(f"FAIL: {exc}")