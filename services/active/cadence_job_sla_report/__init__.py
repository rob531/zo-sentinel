"""
zo-sentinel service package initializer.

Provides shared utilities for staged services, preserving relative intra‑service
imports across promotion from staged → active. All external HTTP calls include
timeouts to satisfy security guidelines.
"""

from __future__ import annotations

import json
from typing import Any, List

import requests
from sqlalchemy.orm import Session

# Application DB session and models – must be imported verbatim.
from app.db import get_session
from app.models import *  # noqa: F403,F401  (import all models used by services)

__all__ = [
    "get_mesh_memory",
    "get_mesh_scores",
    "get_signal_scores",
    "reset_server_export_api_quarantine",
    "setup_database",
]


def _post_query(endpoint: str, query: str) -> List[dict]:
    """
    Helper to POST a raw SQL query to the ZoComputer store.

    Args:
        endpoint: Relative endpoint on the mesh query service.
        query:   The SQL query string.

    Returns:
        Parsed JSON list from the response.
    """
    url = f"http://127.0.0.1:8772/{endpoint}"
    payload = {"query": query}
    resp = requests.post(url, json=payload, timeout=5)
    resp.raise_for_status()
    return resp.json()


def get_mesh_memory() -> List[dict]:
    """
    Retrieve the full contents of the ``mesh_memory`` table from the ZoComputer
    store.

    Returns:
        A list of dictionaries representing rows.
    """
    return _post_query("query", "SELECT * FROM mesh_memory")


def get_mesh_scores() -> List[dict]:
    """
    Retrieve the full contents of the ``mcp_signal_scores`` table from the
    ZoComputer store.

    Returns:
        A list of dictionaries representing rows.
    """
    return _post_query("query", "SELECT * FROM mcp_signal_scores")


def get_signal_scores() -> List[Any]:
    """
    Retrieve all ``SignalScore`` ORM objects from the application database.

    Returns:
        A list of ``SignalScore`` model instances.
    """
    session: Session = get_session()
    return session.query(SignalScore).all()  # type: ignore[name-defined]


def reset_server_export_api_quarantine() -> dict:
    """
    Instruct the mesh service to reset its export‑API quarantine state.

    Returns:
        The JSON payload returned by the service.
    """
    url = "http://127.0.0.1:8772/reset_quarantine"
    resp = requests.post(url, timeout=5)
    resp.raise_for_status()
    return resp.json()


def setup_database() -> None:
    """
    Placeholder for any database‑initialisation logic required by staged
    services. Currently a no‑op; kept for contract compatibility.
    """
    # Intentionally empty – callers rely on its existence only.
    return None


# --------------------------------------------------------------------------- #
# Self‑test (executed when run as a script)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from unittest.mock import patch

    class _DummyResponse:
        def __init__(self, data: Any):
            self._data = data

        def raise_for_status(self) -> None:
            pass

        def json(self) -> Any:
            return self._data

    def _dummy_post(url: str, json: dict | None = None, timeout: int | None = None):
        # Simple routing based on endpoint name.
        if "reset_quarantine" in url:
            return _DummyResponse({"status": "reset"})
        return _DummyResponse([{"id": 1, "value": "dummy"}])

    with patch("requests.post", side_effect=_dummy_post):
        try:
            _ = get_mesh_memory()
            _ = get_mesh_scores()
            _ = get_signal_scores()
            _ = reset_server_export_api_quarantine()
            setup_database()
            print("PASS")
        except Exception as exc:  # pragma: no cover
            print(f"FAIL: {exc}", file=sys.stderr)
            sys.exit(1)