"""
zo-sentinel auto‑emitted service package core utilities.

Provides shared helpers used across staged and active services:
- Database session handling (app.db)
- HTTP query helper for the ZoComputer write‑service (127.0.0.1:8772)
- Simple endpoint wrappers used by many service modules
- Minimal self‑test executed when run as a script
"""

from __future__ import annotations

import json
from typing import Any, Dict

import requests
from fastapi import Depends, FastAPI

# --------------------------------------------------------------------------- #
# Core app imports – must use the real application models and session factory.
# --------------------------------------------------------------------------- #
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
)

# --------------------------------------------------------------------------- #
# HTTP helper – all write‑service queries go through this function.
# --------------------------------------------------------------------------- #
_WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"
_TIMEOUT = 5  # seconds – satisfies Bandit B113


def _post_query(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    POST a JSON payload to the ZoComputer write‑service and return the decoded
    JSON response.

    The function is deliberately tiny – callers are responsible for constructing
    a valid query that matches a table listed in ``schema/bus_catalog.json``.
    """
    response = requests.post(_WRITE_SERVICE_URL, json=payload, timeout=_TIMEOUT)
    response.raise_for_status()
    return response.json()


# --------------------------------------------------------------------------- #
# Public helpers used throughout the code‑base.
# --------------------------------------------------------------------------- #
def get_db(session=Depends(get_session)):
    """FastAPI dependency that yields a SQLAlchemy session."""
    return session


def get_org_by_id(org_id: int, session=Depends(get_session)):
    """Return the Org row with the given primary‑key, or ``None``."""
    return session.query(Org).filter_by(id=org_id).first()


def get_mesh_memory() -> Dict[str, Any]:
    """Fetch the entire ``mesh_memory`` table from the write‑service."""
    return _post_query({"select": "*", "from": "mesh_memory"})


def mesh_scores_endpoint() -> Dict[str, Any]:
    """Fetch the entire ``mcp_signal_scores`` table from the write‑service."""
    return _post_query({"select": "*", "from": "mcp_signal_scores"})


def get_score_disputes_endpoint() -> Dict[str, Any]:
    """Fetch the entire ``mcp_score_disputes`` table from the write‑service."""
    return _post_query({"select": "*", "from": "mcp_score_disputes"})


def get_mesh_memory_endpoint() -> Dict[str, Any]:
    """Alias for :func:`get_mesh_memory` used by some staged services."""
    return get_mesh_memory()


# --------------------------------------------------------------------------- #
# Minimal self‑test – executed when the module is run directly.
# --------------------------------------------------------------------------- #
def _run_self_test() -> None:
    """
    Run a very small sanity check that the helpers can be imported and called
    without raising exceptions.  Network calls are stubbed out to keep the test
    self‑contained.
    """
    # Preserve the real implementation so we can restore it afterwards.
    real_post = globals().get("_post_query")

    try:
        # Stub out the network call – return an empty dict for any payload.
        globals()["_post_query"] = lambda _: {}

        # Call a representative subset of the public helpers.
        _ = get_mesh_memory()
        _ = mesh_scores_endpoint()
        _ = get_score_disputes_endpoint()

        # If we reach this point, everything behaved as expected.
        print("PASS")
    finally:
        # Restore the original function regardless of test outcome.
        if real_post is not None:
            globals()["_post_query"] = real_post


# --------------------------------------------------------------------------- #
# Script entry‑point.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    _run_self_test()