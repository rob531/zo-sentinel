"""
Shared utilities for staged services.

Provides:
- get_mesh_memory
- get_mesh_scores
- get_signal_scores
- mesh_scores_endpoint
- _dummy_post
- dummy_post_endpoint
- reset_server_export_api_quarantine
- _post_query
- _run_self_test
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import requests
from fastapi import Depends

from app.db import get_session
from app.models import (
    MeshMemory,  # type: ignore
    MeshSignalScore,  # type: ignore
    SignalScore,  # type: ignore
)

# --------------------------------------------------------------------------- #
# Internal helper
# --------------------------------------------------------------------------- #
def _post_query(query: str, *, timeout: float = 5.0) -> List[Dict[str, Any]]:
    """
    Send a raw SQL query to the ZoComputer store.

    Args:
        query: The SQL statement to execute.
        timeout: Seconds before the request aborts.

    Returns:
        List of rows as dictionaries.
    """
    url = "http://127.0.0.1:8772/query"
    payload = {"query": query}
    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def get_mesh_memory(session: Any = Depends(get_session)) -> List[MeshMemory]:
    """
    Retrieve mesh memory records from the ZoComputer store.
    """
    rows = _post_query("SELECT * FROM mesh_memory")
    # Convert raw rows to ORM objects for consistency with other services.
    return [MeshMemory(**row) for row in rows]  # type: ignore[arg-type]


def get_mesh_scores(session: Any = Depends(get_session)) -> List[MeshSignalScore]:
    """
    Retrieve mesh signal scores.
    """
    rows = _post_query("SELECT * FROM mcp_signal_scores")
    return [MeshSignalScore(**row) for row in rows]  # type: ignore[arg-type]


def get_signal_scores(session: Any = Depends(get_session)) -> List[SignalScore]:
    """
    Retrieve generic signal scores.
    """
    rows = _post_query("SELECT * FROM signal_scores")
    return [SignalScore(**row) for row in rows]  # type: ignore[arg-type]


def mesh_scores_endpoint() -> List[Dict[str, Any]]:
    """
    FastAPI‑compatible endpoint returning raw mesh scores.
    """
    return _post_query("SELECT * FROM mcp_signal_scores")


def _dummy_post() -> Dict[str, str]:
    """
    Minimal placeholder used by several services.
    """
    return {"status": "ok"}


def dummy_post_endpoint() -> Dict[str, str]:
    """
    Alias for _dummy_post to satisfy import contracts.
    """
    return _dummy_post()


def reset_server_export_api_quarantine() -> Dict[str, str]:
    """
    Instruct the ZoComputer store to clear any quarantine state.
    """
    url = "http://127.0.0.1:8772/reset_quarantine"
    response = requests.post(url, json={}, timeout=5.0)
    response.raise_for_status()
    return {"result": "reset"}


def _run_self_test() -> None:
    """
    Execute a lightweight sanity check.
    """
    # Verify that the external service is reachable without raising.
    try:
        _post_query("SELECT 1")
    except Exception:
        raise RuntimeError("Self‑test failed: unable to query ZoComputer store")
    # No further checks required; the module is otherwise stateless.


# --------------------------------------------------------------------------- #
# Module metadata
# --------------------------------------------------------------------------- #
__all__ = [
    "get_mesh_memory",
    "get_mesh_scores",
    "get_signal_scores",
    "mesh_scores_endpoint",
    "_dummy_post",
    "dummy_post_endpoint",
    "reset_server_export_api_quarantine",
    "_post_query",
    "_run_self_test",
]

# --------------------------------------------------------------------------- #
# Self‑test entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    try:
        _run_self_test()
        print("PASS")
    except Exception:
        print("FAIL")