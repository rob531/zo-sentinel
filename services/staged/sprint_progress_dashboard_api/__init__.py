# services/staged/__init__.py
"""
Shared utilities for staged services.

Provides:
- get_signal_scores
- get_mesh_memory
- orgs_endpoint
- dummy_post_endpoint
- signal_scores_endpoint
- mesh_scores_endpoint
- _run_self_test (executed when module is run as script)
"""

from __future__ import annotations

import json
from typing import Any, List

import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# App DB dependency (must be imported exactly as specified)
# ----------------------------------------------------------------------
from app.db import get_session  # noqa: F401
from app.models import Org  # type: ignore  # noqa: F401

router = APIRouter()


# ----------------------------------------------------------------------
# Mesh (ZoComputer) query helpers
# ----------------------------------------------------------------------
_MESH_URL = "http://127.0.0.1:8772/query"


def _mesh_query(table: str) -> Any:
    """Query the ZoComputer store for a given table."""
    payload = {"table": table}
    try:
        resp = requests.post(_MESH_URL, json=payload, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc))


def get_signal_scores() -> Any:
    """Return raw signal scores from the mesh store."""
    return _mesh_query("mcp_signal_scores")


def get_mesh_memory() -> Any:
    """Return raw mesh memory from the mesh store."""
    return _mesh_query("mesh_memory")


# ----------------------------------------------------------------------
# DB‑backed endpoints
# ----------------------------------------------------------------------
@router.get("/orgs")
def orgs_endpoint(db: Session = Depends(get_session)) -> List[dict]:
    """Return a list of organisations."""
    orgs = db.query(Org).all()
    return [json.loads(org.to_json()) if hasattr(org, "to_json") else {"id": org.id, "name": getattr(org, "name", None)} for org in orgs]  # type: ignore


# ----------------------------------------------------------------------
# Simple POST endpoints
# ----------------------------------------------------------------------
@router.post("/dummy")
def dummy_post_endpoint(request: Request) -> dict:
    """Echo back a minimal success payload."""
    _ = request  # request is kept for future extension
    return {"status": "ok"}


# ----------------------------------------------------------------------
# Composite endpoints
# ----------------------------------------------------------------------
@router.get("/signal-scores")
def signal_scores_endpoint() -> Any:
    """Expose mesh signal scores via HTTP."""
    return get_signal_scores()


@router.get("/mesh-scores")
def mesh_scores_endpoint() -> Any:
    """Expose mesh memory via HTTP."""
    return get_mesh_memory()


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
def _run_self_test() -> None:
    """Execute a lightweight self‑test; prints PASS on success."""
    # ------------------------------------------------------------------
    # Mock the mesh HTTP endpoint
    # ------------------------------------------------------------------
    class _MockResponse:
        def __init__(self, data: Any):
            self._data = data

        def raise_for_status(self) -> None:
            pass

        def json(self) -> Any:
            return self._data

    def _mock_post(url: str, json: dict, timeout: int) -> _MockResponse:  # noqa: D401
        """Return deterministic payloads for known tables."""
        table = json.get("table")
        if table == "mcp_signal_scores":
            return _MockResponse({"scores": []})
        if table == "mesh_memory":
            return _MockResponse({"memory": []})
        raise RuntimeError(f"Unexpected table request: {table}")

    # Apply mock
    original_post = requests.post
    requests.post = _mock_post  # type: ignore

    # ------------------------------------------------------------------
    # Run checks
    # ------------------------------------------------------------------
    try:
        ss = get_signal_scores()
        assert isinstance(ss, dict) and "scores" in ss, "signal scores payload malformed"

        mm = get_mesh_memory()
        assert isinstance(mm, dict) and "memory" in mm, "mesh memory payload malformed"
    finally:
        # Restore original function regardless of outcome
        requests.post = original_post  # type: ignore

    print("PASS")


# ----------------------------------------------------------------------
# When executed directly, run the self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    _run_self_test()