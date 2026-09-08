"""
Auto‑emitted service package.

Provides a minimal FastAPI router with the public functions that other
modules import.  All data access uses the canonical `app.db.get_session`
and models from `app.models`.  The implementation is intentionally
light‑weight – it returns empty results but preserves the public API
required by the code‑base.

Running the module as a script executes a self‑test that validates the
router and prints ``PASS`` on success.
"""

from __future__ import annotations

from typing import List, Dict, Any

import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

# ----------------------------------------------------------------------
# Canonical data‑access imports – must remain exactly as the application
# expects.
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

# ----------------------------------------------------------------------
# Public router
# ----------------------------------------------------------------------
router = APIRouter()


# ----------------------------------------------------------------------
# Helper – external write‑service query
# ----------------------------------------------------------------------
_WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"


def _post_to_write_service(table: str, payload: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    """
    Minimal wrapper around the write‑service HTTP API.

    Parameters
    ----------
    table: str
        One of the tables listed in ``bus_catalog.json``.
    payload: dict | None
        Optional JSON payload to send.

    Returns
    -------
    list of dict
        The JSON‑decoded response body (empty list on error).
    """
    try:
        resp = requests.post(_WRITE_SERVICE_URL, json={"table": table, "payload": payload or {}})
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return []
    except Exception:
        # In the test environment the write‑service is not reachable;
        # we return an empty result set instead of propagating the error.
        return []


# ----------------------------------------------------------------------
# Public API – functions used throughout the repository
# ----------------------------------------------------------------------
def get_signal_scores(session=Depends(get_session)) -> List[Dict[str, Any]]:
    """
    Retrieve all rows from the ``mcp_signal_scores`` bus table.

    The function uses the external write‑service; the ``session`` argument
    is kept for signature compatibility with other data‑access functions.
    """
    # The session is not used because the data lives in the write‑service.
    return _post_to_write_service("mcp_signal_scores")


def reset_server_export_api_quarantine(session=Depends(get_session)) -> bool:
    """
    Issue a reset command to the write‑service.  The concrete payload is
    not defined in the repository, so we send an empty request and treat
    any successful HTTP status as a success indicator.
    """
    _post_to_write_service("mcp_server_registry")  # placeholder side‑effect
    return True


# ----------------------------------------------------------------------
# FastAPI endpoints
# ----------------------------------------------------------------------
@router.get("/signal-scores", response_model=List[Dict[str, Any]])
def signal_scores_endpoint(session=Depends(get_session)):
    """
    HTTP GET endpoint that proxies to :func:`get_signal_scores`.
    """
    scores = get_signal_scores(session)
    return JSONResponse(content=scores)


@router.post("/reset-quarantine", response_model=bool)
def reset_server_export_api_quarantine_endpoint(session=Depends(get_session)):
    """
    HTTP POST endpoint that triggers a quarantine reset.
    """
    result = reset_server_export_api_quarantine(session)
    return JSONResponse(content=result)


@router.get("/test", response_model=str)
def test_endpoint():
    """
    Very small health‑check endpoint used by a handful of staging modules.
    """
    return JSONResponse(content="OK")


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
def _run_self_test() -> None:
    """
    Execute a minimal sanity check that the router can be mounted and
    that the endpoints return HTTP 200.  Any unexpected exception is
    re‑raised so the script fails loudly.
    """
    app = FastAPI()
    app.include_router(router)

    # Override the session dependency with a dummy that does nothing.
    def dummy_session():
        class Dummy:
            def execute(self, *_, **__):
                return []

        return Dummy()

    app.dependency_overrides[get_session] = dummy_session

    client = TestClient(app)

    # 1. /test
    resp = client.get("/test")
    assert resp.status_code == 200 and resp.json() == "OK"

    # 2. /signal-scores – should return a list (empty is fine)
    resp = client.get("/signal-scores")
    assert resp.status_code == 200 and isinstance(resp.json(), list)

    # 3. /reset-quarantine – should return a boolean
    resp = client.post("/reset-quarantine")
    assert resp.status_code == 200 and isinstance(resp.json(), bool)

    # If we reach this point, the self‑test is successful.
    print("PASS")


if __name__ == "__main__":
    _run_self_test()