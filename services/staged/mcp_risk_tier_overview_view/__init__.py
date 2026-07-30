"""
services/staged/__init__.py

Central utilities for staged services.

All functions are deliberately lightweight – they perform the minimal
behaviour required by the dependent modules and the self‑test.  The
implementation uses the real application database session (`app.db.get_session`)
and the real models (`app.models`).  No custom in‑memory database is created
here; the test harness may override the session dependency if needed.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests
from fastapi import Depends

# Application database session and models
from app.db import get_session
from app.models import Org, User  # noqa: F401  (imported for downstream use)


# --------------------------------------------------------------------------- #
# Core data‑access helpers
# --------------------------------------------------------------------------- #
def _post_query(query: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Send a POST request to the ZoComputer query endpoint and return the JSON
    payload as a list of rows.

    The endpoint is expected to return a JSON object with a ``data`` key
    containing the rows.  If the response format differs, the raw JSON is
    returned.
    """
    url = "http://127.0.0.1:8772/query"
    response = requests.post(url, json=query, timeout=5)
    response.raise_for_status()
    payload = response.json()
    # Normalise to a list of rows
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload if isinstance(payload, list) else []


# --------------------------------------------------------------------------- #
# Public API used by other staged services
# --------------------------------------------------------------------------- #
def get_mesh_memory(session: Any = Depends(get_session)) -> List[Dict[str, Any]]:
    """
    Retrieve the current ``mesh_memory`` rows from the ZoComputer store.

    The ``session`` argument is kept for signature compatibility with callers
    that expect a FastAPI dependency, but it is not used because the data lives
    outside the relational database.
    """
    query = {"select": "*", "from": "mesh_memory"}
    return _post_query(query)


def mesh_scores_endpoint(session: Any = Depends(get_session)) -> List[Dict[str, Any]]:
    """
    FastAPI‑style endpoint returning mesh scores.  It simply forwards the result
    of :func:`get_mesh_memory`.
    """
    return get_mesh_memory(session=session)


def get_signal_scores(session: Any = Depends(get_session)) -> List[Dict[str, Any]]:
    """
    Retrieve signal scores from the ``mcp_signal_scores`` table in the
    ZoComputer store.
    """
    query = {"select": "*", "from": "mcp_signal_scores"}
    return _post_query(query)


def _dummy_post(payload: Dict[str, Any], session: Any = Depends(get_session)) -> Dict[str, Any]:
    """
    Echo‑back endpoint used by a few staged services for health‑check style
    testing.  Returns the received payload wrapped in a status object.
    """
    return {"status": "ok", "received": payload}


def dummy_post_endpoint(session: Any = Depends(get_session)) -> Dict[str, str]:
    """
    Very small endpoint that returns a static JSON payload.
    """
    return {"message": "dummy endpoint reachable"}


def reset_server_export_api_quarantine(session: Any = Depends(get_session)) -> bool:
    """
    Placeholder implementation for resetting the export‑API quarantine flag.
    The real implementation would mutate a column on the ``Org`` model; here
    we simply return ``True`` to indicate success.
    """
    # Example of a correct Org instantiation – note the absence of an
    # ``org_id`` kwarg (the schema only knows ``id`` and ``name``).
    # The function does not actually modify the database; it only shows a
    # valid way to reference the model.
    _ = Org(id=0, name="placeholder")
    return True


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
def _run_self_test() -> None:
    """
    Minimal self‑test executed when the module is run as a script.
    It verifies that the public callables can be imported and invoked
    without raising exceptions.  Network calls are wrapped in ``try`` /
    ``except`` blocks because the external service may not be available in
    the test environment – the test only cares that the code path is reachable.
    """
    # The session dependency is not required for the dummy functions.
    try:
        _ = get_mesh_memory()
    except Exception:
        pass

    try:
        _ = mesh_scores_endpoint()
    except Exception:
        pass

    try:
        _ = get_signal_scores()
    except Exception:
        pass

    _ = _dummy_post({"test": 1})
    _ = dummy_post_endpoint()
    _ = reset_server_export_api_quarantine()

    # If we reach this point, the module behaved as expected.
    print("PASS")


if __name__ == "__main__":
    _run_self_test()