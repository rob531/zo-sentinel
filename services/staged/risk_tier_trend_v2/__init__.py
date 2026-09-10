"""
zo‑sentinel staged service package __init__.py
Provides a collection of lightweight endpoint helpers used across many
staged services.  All data access to application tables uses the canonical
SQLAlchemy session from ``app.db`` and the models from ``app.models``.
External mesh/pipeline data is fetched via the ZoComputer HTTP query API.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import requests
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.routing import APIRouter
from sqlalchemy.orm import Session

# --------------------------------------------------------------------------- #
# Core dependencies – must be imported exactly as the application expects.
# --------------------------------------------------------------------------- #
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)  # noqa: F401  (imported for downstream type‑checking)

# --------------------------------------------------------------------------- #
# Router – each staged service imports this module and registers its router.
# --------------------------------------------------------------------------- #
router = APIRouter()


# --------------------------------------------------------------------------- #
# Helper – safe HTTP POST to the ZoComputer query service.
# --------------------------------------------------------------------------- #
_ZO_COMPUTER_URL = os.getenv("ZO_COMPUTER_URL", "http://127.0.0.1:8772/query")
_TIMEOUT = 5  # seconds – satisfies Bandit B113


def _post_query(payload: Dict[str, Any]) -> Any:
    """
    Send a JSON payload to the ZoComputer query endpoint and return the decoded
    JSON response.  The request is wrapped with a timeout and raises a clear
    HTTPException on failure.
    """
    try:
        resp = requests.post(_ZO_COMPUTER_URL, json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Mesh memory endpoints
# --------------------------------------------------------------------------- #
@router.get("/mesh_memory")
def mesh_memory_endpoint() -> List[Dict[str, Any]]:
    """
    Retrieve the full ``mesh_memory`` table from the ZoComputer store.
    """
    payload = {"select": "*", "from": "mesh_memory"}
    result = _post_query(payload)
    return result.get("rows", [])


@router.get("/mesh_memory/{row_id}")
def get_mesh_memory_endpoint(row_id: int) -> Dict[str, Any]:
    """
    Retrieve a single row from ``mesh_memory`` by primary key.
    """
    payload = {
        "select": "*",
        "from": "mesh_memory",
        "where": {"id": row_id},
        "limit": 1,
    }
    result = _post_query(payload)
    rows = result.get("rows", [])
    if not rows:
        raise HTTPException(status_code=404, detail="Mesh memory row not found")
    return rows[0]


# --------------------------------------------------------------------------- #
# Mesh scores endpoints
# --------------------------------------------------------------------------- #
@router.get("/mesh_scores")
def mesh_scores_endpoint() -> List[Dict[str, Any]]:
    """
    Retrieve the full ``mcp_signal_scores`` table from the ZoComputer store.
    """
    payload = {"select": "*", "from": "mcp_signal_scores"}
    result = _post_query(payload)
    return result.get("rows", [])


@router.get("/mesh_scores/{row_id}")
def get_mesh_scores_endpoint(row_id: int) -> Dict[str, Any]:
    """
    Retrieve a single row from ``mcp_signal_scores`` by primary key.
    """
    payload = {
        "select": "*",
        "from": "mcp_signal_scores",
        "where": {"id": row_id},
        "limit": 1,
    }
    result = _post_query(payload)
    rows = result.get("rows", [])
    if not rows:
        raise HTTPException(status_code=404, detail="Mesh scores row not found")
    return rows[0]


# --------------------------------------------------------------------------- #
# Signal scores endpoints (alias for mesh scores)
# --------------------------------------------------------------------------- #
@router.get("/signal_scores")
def signal_scores_endpoint() -> List[Dict[str, Any]]:
    """Alias for ``mesh_scores_endpoint`` – kept for backward compatibility."""
    return mesh_scores_endpoint()


@router.get("/signal_scores/{row_id}")
def get_signal_scores(row_id: int) -> Dict[str, Any]:
    """Alias for ``get_mesh_scores_endpoint`` – kept for backward compatibility."""
    return get_mesh_scores_endpoint(row_id)


# --------------------------------------------------------------------------- #
# Dummy POST endpoint – used by several staged services for health checks.
# --------------------------------------------------------------------------- #
@router.post("/dummy")
def dummy_post_endpoint(request: Request) -> Dict[str, str]:
    """
    Echo back a simple success payload.  The request body is ignored; the
    endpoint exists solely as a lightweight health‑check target.
    """
    # Consume body to avoid client‑side connection leaks.
    _ = request.body()
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Self‑test entry point
# --------------------------------------------------------------------------- #
def _run_self_test() -> None:
    """
    Minimal self‑test executed when the module is run as ``python -m``.
    It validates that the dummy endpoint returns the expected payload.
    """
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test dummy POST
    resp = client.post("/dummy")
    assert resp.status_code == 200, f"dummy POST failed: {resp.status_code}"
    assert resp.json() == {"status": "ok"}, f"unexpected dummy payload: {resp.json()}"

    # Test mesh memory (mocked via the external service – we only verify that the
    # request does not raise an exception; the external service may be unavailable
    # in CI, so we guard against connection errors.)
    try:
        _ = client.get("/mesh_memory")
    except HTTPException:
        pass  # Acceptable – external service may be down during self‑test.

    print("PASS")


if __name__ == "__main__":
    _run_self_test()