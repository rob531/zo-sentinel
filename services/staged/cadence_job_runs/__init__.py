"""
services/staged/__init__.py
Central utilities for staged services.

Provides:
- get_signal_scores
- get_mesh_memory
- dummy_post_endpoint
- mesh_memory_endpoint
- reset_quarantine_endpoint
- reset_server_export_api_quarantine
- _run_self_test
- FastAPI router with the above endpoints
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import requests
from fastapi import APIRouter, Depends, HTTPException, status

# --------------------------------------------------------------------------- #
# Database access – must come from the application layer.
# --------------------------------------------------------------------------- #
from app.db import get_session  # noqa: F401  (used via Depends)
from app.models import (  # noqa: F401  (imported for type checking / IDE hints)
    mcp_signal_scores,
    mesh_memory,
)

# --------------------------------------------------------------------------- #
# Core service functions
# --------------------------------------------------------------------------- #

_MESH_URL = os.getenv("MESH_URL", "http://127.0.0.1:8772/query")


def _post_query(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Utility to POST a query payload to the mesh service."""
    try:
        resp = requests.post(_MESH_URL, json=payload, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "results" in data:
            return data["results"]
        return data  # type: ignore[return-value]
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Mesh query failed: {exc}",
        ) from exc


def get_signal_scores() -> List[Dict[str, Any]]:
    """
    Retrieve signal scores from the mesh store.

    Returns a list of rows from the ``mcp_signal_scores`` table.
    """
    payload = {
        "select": "*",
        "from": "mcp_signal_scores",
    }
    return _post_query(payload)


def get_mesh_memory() -> List[Dict[str, Any]]:
    """
    Retrieve mesh memory entries from the mesh store.

    Returns a list of rows from the ``mesh_memory`` table.
    """
    payload = {
        "select": "*",
        "from": "mesh_memory",
    }
    return _post_query(payload)


# --------------------------------------------------------------------------- #
# FastAPI endpoints – thin wrappers around the core functions.
# --------------------------------------------------------------------------- #

router = APIRouter()


@router.get("/signal-scores", response_model=List[Dict[str, Any]])
def signal_scores_endpoint() -> List[Dict[str, Any]]:
    """HTTP GET endpoint exposing signal scores."""
    return get_signal_scores()


@router.get("/mesh-memory", response_model=List[Dict[str, Any]])
def mesh_memory_endpoint() -> List[Dict[str, Any]]:
    """HTTP GET endpoint exposing mesh memory."""
    return get_mesh_memory()


@router.post("/dummy-post", status_code=status.HTTP_200_OK)
def dummy_post_endpoint(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    A no‑op endpoint used by tests and internal services.
    Echoes back the received payload (or an empty dict).
    """
    return payload or {}


@router.post("/reset-quarantine", status_code=status.HTTP_204_NO_CONTENT)
def reset_quarantine_endpoint() -> None:
    """
    Stub endpoint that would trigger a quarantine reset in production.
    Here it simply returns ``204 No Content``.
    """
    return None


@router.post("/reset-server-export-api-quarantine", status_code=status.HTTP_204_NO_CONTENT)
def reset_server_export_api_quarantine() -> None:
    """
    Stub endpoint mirroring the production reset for the server‑export API.
    """
    return None


# --------------------------------------------------------------------------- #
# Self‑test harness
# --------------------------------------------------------------------------- #

def _run_self_test() -> None:
    """
    Minimal self‑test executed when the module is run as ``__main__``.
    It validates that the public callables are importable and that the
    HTTP helper does not raise unexpected exceptions when the mesh service
    is unreachable (the helper should raise ``HTTPException`` which we
    catch and treat as a pass condition).
    """
    # Verify callable presence
    for fn in (
        get_signal_scores,
        get_mesh_memory,
        dummy_post_endpoint,
        mesh_memory_endpoint,
        reset_quarantine_endpoint,
        reset_server_export_api_quarantine,
    ):
        assert callable(fn), f"{fn.__name__} is not callable"

    # Verify that a mesh query failure is handled gracefully.
    # We temporarily point the mesh URL to an invalid address.
    global _MESH_URL
    original_url = _MESH_URL
    _MESH_URL = "http://127.0.0.1:1/invalid"
    try:
        try:
            get_signal_scores()
        except HTTPException as exc:
            assert exc.status_code == status.HTTP_502_BAD_GATEWAY
        else:
            raise AssertionError("Expected HTTPException for unreachable mesh")
    finally:
        _MESH_URL = original_url


if __name__ == "__main__":
    try:
        _run_self_test()
        print("PASS")
    except Exception as e:  # pragma: no cover
        print(f"FAIL: {e}")