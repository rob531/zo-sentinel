"""
Shared utilities for staged services.

Provides:
- get_mesh_memory
- get_signal_scores
- mesh_scores_endpoint
- signal_scores_endpoint
- _dummy_post
- dummy_post_endpoint
- reset_server_export_api_quarantine
- _run_self_test
"""

from __future__ import annotations

import json
from typing import Any, Dict

import requests
from fastapi import Depends

from app.db import get_session
from app.models import *  # noqa: F403,F401  (import all models used by services)


# --------------------------------------------------------------------------- #
# Internal helper to query the ZoComputer store
# --------------------------------------------------------------------------- #
def _post_query(sql: str) -> Dict[str, Any]:
    """POST a raw SQL query to the ZoComputer query endpoint.

    Args:
        sql: The SQL statement to execute.

    Returns:
        The JSON response parsed as a dict.
    """
    url = "http://127.0.0.1:8772/query"
    payload = {"sql": sql}
    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, data=json.dumps(payload), headers=headers, timeout=5)
    resp.raise_for_status()
    return resp.json()


# --------------------------------------------------------------------------- #
# Public API used by the various staged services
# --------------------------------------------------------------------------- #
def get_mesh_memory(session=Depends(get_session)) -> Dict[str, Any]:
    """Fetch the current mesh memory snapshot."""
    # The DB session is kept for contract compatibility; it is not used here.
    _ = session  # silence unused‑variable warnings
    return _post_query("SELECT * FROM mesh_memory")


def get_signal_scores(session=Depends(get_session)) -> Dict[str, Any]:
    """Fetch the latest signal scores."""
    _ = session
    return _post_query("SELECT * FROM mcp_signal_scores")


def mesh_scores_endpoint(session=Depends(get_session)) -> Dict[str, Any]:
    """Endpoint wrapper used by services that expose mesh scores."""
    _ = session
    return get_mesh_memory()


def signal_scores_endpoint(session=Depends(get_session)) -> Dict[str, Any]:
    """Endpoint wrapper used by services that expose signal scores."""
    _ = session
    return get_signal_scores()


def _dummy_post(session=Depends(get_session)) -> Dict[str, Any]:
    """Placeholder POST endpoint – returns a static payload."""
    _ = session
    return {"status": "ok", "message": "dummy post received"}


def dummy_post_endpoint(session=Depends(get_session)) -> Dict[str, Any]:
    """Public wrapper for the dummy POST endpoint."""
    _ = session
    return _dummy_post()


def reset_server_export_api_quarantine(session=Depends(get_session)) -> Dict[str, Any]:
    """Trigger a reset of the server export API quarantine flag."""
    _ = session
    return _post_query("SELECT reset_server_export_api_quarantine()")


def _run_self_test() -> None:
    """Execute a minimal self‑test; raises on failure."""
    # Import here to avoid circular imports in production.
    from unittest.mock import patch, MagicMock

    dummy_response = MagicMock()
    dummy_response.raise_for_status = MagicMock()
    dummy_response.json.return_value = {"data": []}

    with patch("requests.post", return_value=dummy_response):
        # The session dependency is overridden with a no‑op stub.
        class _StubSession:
            pass

        # Call each public function to ensure they return without error.
        assert isinstance(get_mesh_memory(_StubSession()), dict)
        assert isinstance(get_signal_scores(_StubSession()), dict)
        assert isinstance(mesh_scores_endpoint(_StubSession()), dict)
        assert isinstance(signal_scores_endpoint(_StubSession()), dict)
        assert isinstance(_dummy_post(_StubSession()), dict)
        assert isinstance(dummy_post_endpoint(_StubSession()), dict)
        assert isinstance(reset_server_export_api_quarantine(_StubSession()), dict)


# --------------------------------------------------------------------------- #
# Self‑test entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    try:
        _run_self_test()
        print("PASS")
    except Exception as exc:  # pragma: no cover
        print(f"FAIL: {exc}")