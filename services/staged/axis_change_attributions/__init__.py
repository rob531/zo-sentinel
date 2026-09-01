"""
Auto‑emitted service package for staged → active promotion.

Provides shared utilities and FastAPI router endpoints used across
the staged services. All data access is performed via the application
SQLAlchemy session (`app.db.get_session`) and the models defined in
`app.models`. External mesh queries are performed via HTTP POST with
a timeout to satisfy security guidelines.
"""

from __future__ import annotations

import json
from typing import Any, List

import requests
from fastapi import APIRouter, Depends, HTTPException, status

from app.db import get_session
from app.models import MeshMemory, MeshScore, SignalScore, ServerExportApiQuarantine  # type: ignore

# --------------------------------------------------------------------------- #
# FastAPI router
# --------------------------------------------------------------------------- #
router = APIRouter()


# --------------------------------------------------------------------------- #
# Helper – external mesh query
# --------------------------------------------------------------------------- #
_MESH_ENDPOINT = "http://127.0.0.1:8772/query"
_TIMEOUT = 5  # seconds – satisfies Bandit B113


def _post_mesh(payload: dict) -> Any:
    """POST a JSON payload to the mesh service with a timeout."""
    try:
        response = requests.post(_MESH_ENDPOINT, json=payload, timeout=_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Mesh service error: {exc}",
        ) from exc


# --------------------------------------------------------------------------- #
# Public API – DB access
# --------------------------------------------------------------------------- #
def get_mesh_memory(session=Depends(get_session)) -> List[dict]:
    """Return all rows from the `mesh_memory` table as dicts."""
    records = session.query(MeshMemory).all()
    return [r.__dict__ for r in records]


def get_mesh_scores(session=Depends(get_session)) -> List[dict]:
    """Return all rows from the `mesh_score` table as dicts."""
    records = session.query(MeshScore).all()
    return [r.__dict__ for r in records]


def get_signal_scores(session=Depends(get_session)) -> List[dict]:
    """Return all rows from the `signal_score` table as dicts."""
    records = session.query(SignalScore).all()
    return [r.__dict__ for r in records]


def reset_server_export_api_quarantine(session=Depends(get_session)) -> dict:
    """
    Reset the `ServerExportApiQuarantine` flag for all rows.
    Returns a summary of the operation.
    """
    updated = session.query(ServerExportApiQuarantine).update(
        {"is_quarantined": False}
    )
    session.commit()
    return {"rows_updated": updated}


# --------------------------------------------------------------------------- #
# FastAPI endpoints (router)
# --------------------------------------------------------------------------- #
@router.get("/mesh_memory", response_model=List[dict])
def mesh_memory_endpoint(session=Depends(get_session)):
    """FastAPI endpoint exposing mesh memory."""
    return get_mesh_memory(session)


@router.get("/mesh_scores", response_model=List[dict])
def mesh_scores_endpoint(session=Depends(get_session)):
    """FastAPI endpoint exposing mesh scores."""
    return get_mesh_scores(session)


@router.get("/signal_scores", response_model=List[dict])
def signal_scores_endpoint(session=Depends(get_session)):
    """FastAPI endpoint exposing signal scores."""
    return get_signal_scores(session)


@router.post("/reset_quarantine", status_code=status.HTTP_200_OK)
def reset_quarantine_endpoint(session=Depends(get_session)):
    """FastAPI endpoint to reset the export‑API quarantine flag."""
    return reset_server_export_api_quarantine(session)


@router.post("/dummy", status_code=status.HTTP_200_OK)
def dummy_post_endpoint():
    """A placeholder POST endpoint used by several staged services."""
    return {"result": "ok"}


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
def _run_self_test() -> bool:
    """
    Minimal self‑test executed when the module is run as a script.
    It performs a harmless DB query using the current session
    (which may be overridden in tests) and returns True on success.
    """
    # The session dependency is resolved at call time; in a test
    # environment the caller can override it with an in‑memory SQLite.
    from fastapi import Depends

    session = Depends(get_session)()
    # Perform a lightweight query – existence check only.
    session.query(MeshMemory).limit(1).all()
    return True


if __name__ == "__main__":
    if _run_self_test():
        print("PASS")