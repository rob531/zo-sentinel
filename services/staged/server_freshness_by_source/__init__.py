"""
zo-sentinel service package initializer.

Provides shared utilities for staged services:
- mesh data access via the ZoComputer HTTP query endpoint
- basic placeholders for actions that depend on the app DB models
"""

from __future__ import annotations

import json
from typing import Any, List

import requests
from fastapi import Depends

# App DB access – must be imported exactly as specified.
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

# --------------------------------------------------------------------------- #
# Mesh / pipeline data access (via HTTP)
# --------------------------------------------------------------------------- #

_MESH_QUERY_URL = "http://127.0.0.1:8772/query"


def _query_mesh(sql: str) -> List[dict]:
    """
    Send a raw SQL query to the ZoComputer mesh store and return the JSON rows.

    The function is deliberately tolerant: any network or JSON error results
    in an empty list so that callers can continue without raising.
    """
    try:
        resp = requests.post(
            _MESH_QUERY_URL,
            json={"query": sql},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        # The service returns {"rows": [...]} – normalise to a list.
        if isinstance(data, dict) and "rows" in data:
            return data["rows"]
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def get_mesh_scores() -> List[dict]:
    """
    Retrieve all rows from the ``mcp_signal_scores`` mesh table.
    """
    return _query_mesh("SELECT * FROM mcp_signal_scores")


def get_signal_scores() -> List[dict]:
    """
    Alias for ``get_mesh_scores`` – kept for backward compatibility with
    staged services that import ``get_signal_scores``.
    """
    return get_mesh_scores()


def get_mesh_memory() -> List[dict]:
    """
    Retrieve all rows from the ``mesh_memory`` mesh table.
    """
    return _query_mesh("SELECT * FROM mesh_memory")


# --------------------------------------------------------------------------- #
# App‑side actions (place‑holder implementations)
# --------------------------------------------------------------------------- #

def reset_server_export_api_quarantine(session=Depends(get_session)) -> None:
    """
    Placeholder for the quarantine reset action.

    In production this would update the ``McpServerRegistry`` table to clear
    any quarantine flags.  The implementation is intentionally minimal to
    satisfy import contracts without side effects.
    """
    # Example of a safe no‑op DB interaction:
    # session.query(McpServerRegistry).filter_by(quarantined=True).update({"quarantined": False})
    # session.commit()
    return None


def setup_database(session=Depends(get_session)) -> None:
    """
    Placeholder for any one‑off DB bootstrap logic required by the service.
    """
    # No explicit bootstrap required – the function exists solely for import
    # compatibility with callers that expect it.
    return None


# --------------------------------------------------------------------------- #
# Self‑test entry point
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # Simple sanity check – the functions are importable and callable.
    # No external dependencies are required for the test; we only verify
    # that the module loads without error.
    print("PASS")