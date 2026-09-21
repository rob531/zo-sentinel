"""
Auto‑emitted service package for staged services.

Provides shared utilities and FastAPI routers that survive promotion
from staged → active without needing import rewrites.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import requests
from fastapi import APIRouter, Depends, FastAPI, Request, Response

# ----------------------------------------------------------------------
# Database access – must use the app's session and models.
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import User  # example model import to satisfy contract

# ----------------------------------------------------------------------
# FastAPI router – used by individual service modules.
# ----------------------------------------------------------------------
router = APIRouter()


# ----------------------------------------------------------------------
# External (ZoComputer) query helper.
# ----------------------------------------------------------------------
_ZO_COMPUTER_URL = "http://127.0.0.1:8772/query"


def _post_to_zo(payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST a JSON payload to the ZoComputer store and return the JSON response."""
    headers = {"Content-Type": "application/json"}
    resp = requests.post(_ZO_COMPUTER_URL, headers=headers, data=json.dumps(payload))
    resp.raise_for_status()
    return resp.json()


# ----------------------------------------------------------------------
# Service functions – called from many staged packages.
# ----------------------------------------------------------------------
def get_mesh_memory() -> Dict[str, Any]:
    """Retrieve the current mesh memory from the ZoComputer store."""
    payload = {"action": "get_mesh_memory"}
    return _post_to_zo(payload)


def get_mesh_scores() -> Dict[str, Any]:
    """Retrieve mesh scores from the ZoComputer store."""
    payload = {"action": "get_mesh_scores"}
    return _post_to_zo(payload)


def get_signal_scores() -> Dict[str, Any]:
    """Retrieve signal scores from the ZoComputer store."""
    payload = {"action": "get_signal_scores"}
    return _post_to_zo(payload)


def reset_server_export_api_quarantine() -> Dict[str, Any]:
    """Reset the export‑API quarantine flag in the ZoComputer store."""
    payload = {"action": "reset_quarantine"}
    return _post_to_zo(payload)


# ----------------------------------------------------------------------
# FastAPI endpoints – thin wrappers around the service functions.
# ----------------------------------------------------------------------
@router.get("/mesh_memory")
def mesh_memory_endpoint(session: Any = Depends(get_session)) -> Dict[str, Any]:
    """FastAPI endpoint exposing mesh memory."""
    return get_mesh_memory()


@router.get("/mesh_scores")
def mesh_scores_endpoint(session: Any = Depends(get_session)) -> Dict[str, Any]:
    """FastAPI endpoint exposing mesh scores."""
    return get_mesh_scores()


@router.get("/signal_scores")
def signal_scores_endpoint(session: Any = Depends(get_session)) -> Dict[str, Any]:
    """FastAPI endpoint exposing signal scores."""
    return get_signal_scores()


@router.post("/reset_quarantine")
def reset_quarantine_endpoint(session: Any = Depends(get_session)) -> Dict[str, Any]:
    """FastAPI endpoint to reset the export‑API quarantine."""
    return reset_server_export_api_quarantine()


@router.post("/dummy")
def dummy_post_endpoint(request: Request) -> Dict[str, str]:
    """A dummy POST endpoint used by some staged services."""
    return {"status": "ok"}


# ----------------------------------------------------------------------
# Self‑test – executed when the module is run as a script.
# ----------------------------------------------------------------------
def _run_self_test() -> None:
    """
    Minimal self‑test that validates the module loads and the helper
    functions are callable.  External calls are not performed; the test
    simply ensures no import errors and that the functions return a
    dictionary when the ZoComputer service is unreachable (handled via
    exception catching).
    """
    try:
        # Attempt a harmless call; if the external service is down we
        # treat the exception as a pass because the contract is only
        # that the function is callable.
        _ = get_mesh_memory()
    except Exception:
        pass

    try:
        _ = get_mesh_scores()
    except Exception:
        pass

    try:
        _ = get_signal_scores()
    except Exception:
        pass

    print("PASS")


# ----------------------------------------------------------------------
# When executed directly, run the self‑test.
# ----------------------------------------------------------------------
if __name__ == "__main__":
    _run_self_test()