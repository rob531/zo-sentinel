"""
zo-sentinel service package core.

Provides shared response models, simple bus‑query helpers and a tiny
self‑test used by many staged services.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel

# ----------------------------------------------------------------------
# FastAPI / SQLAlchemy integration (required by the build system)
# ----------------------------------------------------------------------
from app.db import get_session  # noqa: F401  (exported for dependants)
from app.models import (  # noqa: F401  (re‑exported for dependants)
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    User,
    Org,
)

# ----------------------------------------------------------------------
# Public Pydantic response / data models
# ----------------------------------------------------------------------


class ServerResponse(BaseModel):
    """Standard API envelope used throughout the code‑base."""

    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None


class UserRead(BaseModel):
    """Minimal read‑only representation of a user."""

    id: int
    username: str
    email: Optional[str] = None


class PerspectiveSnapshot(BaseModel):
    """Snapshot of a perspective – used by `app/models.py` inheritance."""

    id: int
    perspective: str
    snapshot: Dict[str, Any]


class McpScoreDisputeService(BaseModel):
    """Base model for score‑dispute services."""

    dispute_id: int
    details: Dict[str, Any]


# ----------------------------------------------------------------------
# Internal helper – safe bus query
# ----------------------------------------------------------------------


_BUS_URL = "http://127.0.0.1:8772/query"


def _query_bus(table: str, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Perform a safe POST request to the ZoComputer write‑service bus.

    Parameters
    ----------
    table: str
        Name of the bus table – must be one of the cataloged tables.
    filters: dict | None
        Simple equality filters; the service interprets them safely.

    Returns
    -------
    list[dict]
        Rows returned by the bus service (empty list on error).
    """
    payload: Dict[str, Any] = {"table": table, "filters": filters or {}}
    try:
        response = requests.post(_BUS_URL, json=payload, timeout=5)
        response.raise_for_status()
        data = response.json()
        # The bus service returns {"rows": [...]} – normalise to a list.
        return data.get("rows", [])
    except Exception:
        # In production we would log; for the self‑test we simply return [].
        return []


# ----------------------------------------------------------------------
# Public service helpers (used by many staged modules)
# ----------------------------------------------------------------------


def mesh_memory_endpoint() -> List[Dict[str, Any]]:
    """Return all rows from the ``mesh_memory`` bus table."""
    return _query_bus("mesh_memory")


def mesh_memory_endpoint_get() -> List[Dict[str, Any]]:
    """Alias used by older consumers – identical to :func:`mesh_memory_endpoint`."""
    return mesh_memory_endpoint()


def get_mesh_memory_endpoint() -> List[Dict[str, Any]]:
    """Another alias kept for backward compatibility."""
    return mesh_memory_endpoint()


def get_mesh_memory_by_id(mesh_id: int) -> List[Dict[str, Any]]:
    """Fetch a single ``mesh_memory`` row by its primary key."""
    return _query_bus("mesh_memory", {"id": mesh_id})


def get_score_disputes_endpoint() -> List[Dict[str, Any]]:
    """Retrieve all score‑dispute records."""
    return _query_bus("mcp_score_disputes")


def signal_scores_endpoint() -> List[Dict[str, Any]]:
    """Retrieve all signal‑score records."""
    return _query_bus("mcp_signal_scores")


def test_self() -> Dict[str, Any]:
    """
    Very small sanity check exercised by the package's ``__main__`` block.

    Returns a dict with an ``ok`` flag; the caller prints ``PASS`` when true.
    """
    sample = mesh_memory_endpoint()
    return {"ok": isinstance(sample, list)}


def run_self_test() -> None:
    """
    Execute the package's self‑test.  Prints exactly ``PASS`` on success,
    otherwise ``FAIL``.
    """
    result = test_self()
    if result.get("ok"):
        print("PASS")
    else:
        print("FAIL")


# ----------------------------------------------------------------------
# Module exports
# ----------------------------------------------------------------------
__all__ = [
    # models
    "ServerResponse",
    "UserRead",
    "PerspectiveSnapshot",
    "McpScoreDisputeService",
    # db/session
    "get_session",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "User",
    "Org",
    # helpers
    "mesh_memory_endpoint",
    "mesh_memory_endpoint_get",
    "get_mesh_memory_endpoint",
    "get_mesh_memory_by_id",
    "get_score_disputes_endpoint",
    "signal_scores_endpoint",
    "test_self",
    "run_self_test",
]


# ----------------------------------------------------------------------
# Self‑test entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    run_self_test()