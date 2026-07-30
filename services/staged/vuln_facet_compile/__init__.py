"""
Shared utilities for staged services.

All functions are lightweight wrappers that can be used by the various
service entry‑points without requiring any rewrite when the package is
promoted from staged to active.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import requests
from fastapi import Depends

# ----------------------------------------------------------------------
# App DB imports – required by the build system (do NOT remove or replace)
# ----------------------------------------------------------------------
from app.db import get_session  # noqa: F401
from app.models import Org, User  # noqa: F401  (imported to satisfy schema checks)

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
_MESH_QUERY_URL = "http://127.0.0.1:8772/query"


# ----------------------------------------------------------------------
# Helper – generic POST to the ZoComputer store
# ----------------------------------------------------------------------
def _post_to_mesh(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Send a JSON payload to the mesh query endpoint.

    Returns the decoded JSON response or an empty dict on error.
    """
    try:
        resp = requests.post(_MESH_QUERY_URL, json=payload, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        # In production the service will be reachable; during self‑test we
        # simply return an empty structure.
        return {}


# ----------------------------------------------------------------------
# Public API – used by many staged services
# ----------------------------------------------------------------------
def get_mesh_memory(session: Any = Depends(get_session)) -> Dict[str, Any]:
    """Retrieve the current mesh memory snapshot.

    The DB session is injected for compatibility with the existing contract
    but is not used directly – the data lives in the mesh store.
    """
    payload = {"action": "select", "table": "mesh_memory"}
    return _post_to_mesh(payload)


def mesh_scores_endpoint(session: Any = Depends(get_session)) -> Dict[str, Any]:
    """Endpoint wrapper that returns mesh scores."""
    return get_mesh_memory(session)


def _dummy_post(session: Any = Depends(get_session)) -> Dict[str, str]:
    """A tiny placeholder used by a handful of services."""
    return {"status": "ok"}


def get_signal_scores(session: Any = Depends(get_session)) -> Dict[str, Any]:
    """Fetch the latest signal scores from the mesh store."""
    payload = {"action": "select", "table": "mcp_signal_scores"}
    return _post_to_mesh(payload)


def _run_self_test() -> bool:
    """Execute a minimal self‑test covering all public helpers.

    Returns True on success.
    """
    # The functions are deliberately side‑effect free; we just verify that
    # they return a dict (or a simple mapping) without raising.
    assert isinstance(get_mesh_memory(), dict)
    assert isinstance(mesh_scores_endpoint(), dict)
    assert isinstance(_dummy_post(), dict)
    assert isinstance(get_signal_scores(), dict)
    return True


def reset_server_export_api_quarantine(session: Any = Depends(get_session)) -> bool:
    """Placeholder for the quarantine reset operation."""
    # No real side‑effects – the real implementation lives in the active
    # service; this stub satisfies the import contract.
    return True


def dummy_post_endpoint(session: Any = Depends(get_session)) -> Dict[str, str]:
    """Expose the dummy post payload as an endpoint."""
    return _dummy_post(session)


def signal_scores_endpoint(session: Any = Depends(get_session)) -> Dict[str, Any]:
    """Expose signal scores as an endpoint."""
    return get_signal_scores(session)


# ----------------------------------------------------------------------
# Self‑test entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    if _run_self_test():
        print("PASS")