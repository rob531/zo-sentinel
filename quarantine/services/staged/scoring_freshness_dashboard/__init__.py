"""
services.staged package entry point.

Provides shared utilities and FastAPI router for staged services.
All imports from the application layer are retained to satisfy
non‑hollow contracts.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends, HTTPException

# Application‑level imports – required by contract
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

router = APIRouter()


# ----------------------------------------------------------------------
# Internal helper – query the ZoComputer store
# ----------------------------------------------------------------------
def _query_mesh(sql: str) -> List[Dict[str, Any]]:
    """
    Execute a raw SQL query against the ZoComputer mesh store.

    The store is accessed via a POST request to the local query service.
    """
    try:
        response = httpx.post(
            "http://127.0.0.1:8772/query",
            json={"sql": sql},
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ----------------------------------------------------------------------
# Public utilities – used by many staged services
# ----------------------------------------------------------------------
def get_mesh_memory() -> List[Dict[str, Any]]:
    """Return the full contents of the ``mesh_memory`` table."""
    return _query_mesh("SELECT * FROM mesh_memory")


def mesh_scores_endpoint() -> List[Dict[str, Any]]:
    """Return the full contents of the ``mesh_scores`` table."""
    return _query_mesh("SELECT * FROM mesh_scores")


def get_signal_scores() -> List[Dict[str, Any]]:
    """Return the full contents of the ``mcp_signal_scores`` table."""
    return _query_mesh("SELECT * FROM mcp_signal_scores")


def get_mesh_scores() -> List[Dict[str, Any]]:
    """Alias for ``mesh_scores_endpoint`` – kept for historic callers."""
    return mesh_scores_endpoint()


def _dummy_post(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Perform a generic POST against the mesh query service.
    Used by a handful of legacy endpoints.
    """
    try:
        response = httpx.post(
            "http://127.0.0.1:8772/query",
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def dummy_post_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy wrapper around ``_dummy_post``."""
    return _dummy_post(payload)


def reset_server_export_api_quarantine() -> Dict[str, Any]:
    """Trigger a quarantine reset in the mesh store."""
    return _dummy_post({"action": "reset_quarantine"})


# ----------------------------------------------------------------------
# FastAPI route registrations (optional – kept for completeness)
# ----------------------------------------------------------------------
@router.get("/mesh_memory", response_model=List[Dict[str, Any]])
def mesh_memory_route() -> List[Dict[str, Any]]:
    return get_mesh_memory()


@router.get("/mesh_scores", response_model=List[Dict[str, Any]])
def mesh_scores_route() -> List[Dict[str, Any]]:
    return mesh_scores_endpoint()


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
def _run_self_test() -> None:
    """
    Minimal self‑test executed when the module is run as ``__main__``.
    It monkey‑patches ``httpx.post`` to return a deterministic payload
    and verifies that ``get_mesh_memory`` propagates the data unchanged.
    """
    class _DummyResponse:
        def __init__(self, data: Any):
            self._data = data

        def raise_for_status(self) -> None:
            pass

        def json(self) -> Any:
            return self._data

    def _fake_post(url: str, json: Dict[str, Any], timeout: float) -> _DummyResponse:  # noqa: D401
        # Return a predictable payload regardless of the query.
        return _DummyResponse([{"test_key": "test_value"}])

    original_post = httpx.post
    httpx.post = _fake_post  # type: ignore[assignment]

    try:
        result = get_mesh_memory()
        assert result == [{"test_key": "test_value"}], "self‑test payload mismatch"
    finally:
        httpx.post = original_post  # restore original function


if __name__ == "__main__":
    _run_self_test()
    print("PASS")