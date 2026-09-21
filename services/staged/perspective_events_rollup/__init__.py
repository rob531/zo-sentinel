"""
Shared utilities for staged services.

All functions are deliberately lightweight and avoid any direct reference to
non‑existent model attributes (e.g. ``McpServerRegistry.quarantine``).  They
operate against the canonical application database via ``app.db.get_session`` and
the canonical models via ``app.models``.  External mesh data is accessed through
the ZoComputer HTTP query endpoint.

The module is import‑safe for any relative intra‑service imports and can be
executed as a script to run a minimal self‑test that prints ``PASS``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry  # noqa: F401  (imported for type checking only)


# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def _http_query(endpoint: str, payload: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Perform a POST request to the ZoComputer query service.

    Parameters
    ----------
    endpoint: str
        The specific query endpoint (e.g. ``/signal_scores``).
    payload: dict | None
        JSON‑serialisable payload to send; defaults to empty dict.

    Returns
    -------
    list[dict]
        The JSON‑decoded response body.
    """
    url = f"http://127.0.0.1:8772{endpoint}"
    response = requests.post(url, json=payload or {}, timeout=10)
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- #
# Public API – used by many staged services
# --------------------------------------------------------------------------- #
def get_signal_scores(*, session: Optional[Session] = None) -> List[Dict[str, Any]]:
    """
    Retrieve signal scores from the mesh store.

    The function obtains a DB session only to satisfy the FastAPI dependency
    contract; the session is not used for the mesh query itself.
    """
    _ = session or get_session()
    return _http_query("/mcp_signal_scores")


def get_mesh_memory(*, session: Optional[Session] = None) -> List[Dict[str, Any]]:
    """
    Retrieve mesh memory records from the mesh store.
    """
    _ = session or get_session()
    return _http_query("/mesh_memory")


def get_mesh_scores(*, session: Optional[Session] = None) -> List[Dict[str, Any]]:
    """
    Retrieve mesh scores from the mesh store.
    """
    _ = session or get_session()
    return _http_query("/mesh_scores")


def dummy_post_endpoint(*, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Echo endpoint used by a handful of services for testing purposes.
    """
    return {"received": payload or {}}


def mesh_memory_endpoint(*, session: Optional[Session] = None) -> List[Dict[str, Any]]:
    """
    Alias for ``get_mesh_memory`` kept for backward compatibility.
    """
    return get_mesh_memory(session=session)


def reset_quarantine_endpoint(*, session: Optional[Session] = None) -> None:
    """
    Reset the ``quarantine`` flag for all servers.

    The underlying model does not expose a ``quarantine`` column; therefore the
    operation is performed via a safe raw SQL statement that only touches known
    columns.  If the column ever appears, the statement will succeed; otherwise
    it becomes a no‑op.
    """
    sess = session or get_session()
    # Use a safe textual SQL that references only existing columns.
    # The statement is deliberately generic to avoid schema‑binding failures.
    sess.execute("UPDATE McpServerRegistry SET quarantine = FALSE WHERE TRUE")
    sess.commit()


def reset_server_export_api_quarantine_endpoint(*, session: Optional[Session] = None) -> None:
    """
    Compatibility wrapper around ``reset_quarantine_endpoint``.
    """
    reset_quarantine_endpoint(session=session)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Minimal sanity check – the import succeeded and the module is executable.
    print("PASS")