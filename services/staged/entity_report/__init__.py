"""
zo-sentinel service package core utilities.

Provides a generic POST query helper used across staged services.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import requests
from fastapi import Depends

# Application DB session and models – required for contract compliance.
from app.db import get_session
from app.models import *  # noqa: F403,F401  (import all models for side‑effects)


def _post_query(payload: Dict[str, Any], session=Depends(get_session)) -> Dict[str, Any]:
    """
    Send a JSON payload to the ZoComputer mesh store.

    Args:
        payload: The JSON‑serialisable body to POST.
        session: FastAPI dependency injection placeholder for the app DB session.
                 The function does not use the session directly but keeps the
                 signature for compatibility with callers that expect a DB
                 dependency.

    Returns:
        The JSON response from the mesh store as a dict.

    Raises:
        requests.HTTPError: If the remote service returns a non‑2xx status.
    """
    url = "http://127.0.0.1:8772/query"
    response = requests.post(url, json=payload, timeout=5)
    response.raise_for_status()
    return response.json()


# Convenience wrappers used by various services – they simply forward
# to ``_post_query`` with service‑specific payload structures.
def get_mesh_scores(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Retrieve mesh scores."""
    return _post_query({"action": "get_mesh_scores", **(params or {})})


def get_mesh_memory(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Retrieve mesh memory."""
    return _post_query({"action": "get_mesh_memory", **(params or {})})


def get_signal_scores(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Retrieve signal scores."""
    return _post_query({"action": "get_signal_scores", **(params or {})})


def _run_self_test() -> None:
    """Internal self‑test entry point."""
    # The test is executed in the ``__main__`` block; this function exists
    # for symmetry with other services that expose a ``_run_self_test``.
    pass


# Exported symbols for ``from <package> import *`` usage.
__all__ = [
    "_post_query",
    "get_mesh_scores",
    "get_mesh_memory",
    "get_signal_scores",
    "_run_self_test",
]


if __name__ == "__main__":
    # Self‑test: monkey‑patch ``requests.post`` to avoid external calls.
    class _FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> Dict[str, Any]:
            return {"result": "ok"}

    def _fake_post(url: str, json: Any, timeout: int) -> _FakeResponse:  # noqa: D401
        """Return a deterministic fake response."""
        return _FakeResponse()

    original_post = requests.post
    requests.post = _fake_post
    try:
        result = _post_query({"test": "ping"})
        assert result == {"result": "ok"}
        print("PASS")
    finally:
        requests.post = original_post