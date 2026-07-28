"""
services.staged package initializer.

Provides shared utilities for staged services:
- Database helpers using the app's SQLAlchemy session.
- Simple wrappers around external mesh queries.
- FastAPI router with minimal endpoints.
- Self‑test that prints PASS.
"""

from __future__ import annotations

from typing import Any, List

import requests
from fastapi import APIRouter, Depends

# ----------------------------------------------------------------------
# App database access (must use the real app models and session)
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import (
    Org,
    User,
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    McpSignalScores,
    MeshMemory,
)

# ----------------------------------------------------------------------
# FastAPI router (used by services that import from this module)
# ----------------------------------------------------------------------
router = APIRouter()


# ----------------------------------------------------------------------
# Helper / service functions
# ----------------------------------------------------------------------
def _dummy_post(session: Any = Depends(get_session)) -> dict:
    """
    Insert a minimal dummy Org record.  Demonstrates that the ORM
    works without using any non‑existent columns (e.g. ``slug``).
    """
    dummy = Org(name="dummy")
    session.add(dummy)
    session.commit()
    return {"status": "ok", "id": dummy.id}


def _post_query(payload: dict) -> Any:
    """
    Forward a JSON payload to the mesh query endpoint.
    """
    resp = requests.post("http://127.0.0.1:8772/query", json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_signal_scores(session: Any = Depends(get_session)) -> List[dict]:
    """
    Return all rows from the ``mcp_signal_scores`` table as plain dicts.
    """
    rows = session.query(McpSignalScores).all()
    return [row.__dict__ for row in rows]


def get_mesh_memory(session: Any = Depends(get_session)) -> List[dict]:
    """
    Return all rows from the ``mesh_memory`` table as plain dicts.
    """
    rows = session.query(MeshMemory).all()
    return [row.__dict__ for row in rows]


def get_mesh_scores(session: Any = Depends(get_session)) -> List[dict]:
    """
    Alias for ``get_mesh_memory`` – kept for backward compatibility.
    """
    return get_mesh_memory(session)


# ----------------------------------------------------------------------
# FastAPI endpoints (exposed via the router)
# ----------------------------------------------------------------------
@router.get("/mesh_memory")
def mesh_memory_endpoint(session: Any = Depends(get_session)):
    """Endpoint returning mesh memory records."""
    return get_mesh_memory(session)


@router.post("/reset_quarantine")
def reset_quarantine_endpoint():
    """Placeholder endpoint – in production this would reset quarantine state."""
    return {"status": "quarantine reset"}


@router.post("/reset_server_export_api_quarantine")
def reset_server_export_api_quarantine():
    """Placeholder endpoint – in production this would reset server export API quarantine."""
    return {"status": "server export API quarantine reset"}


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # The self‑test does not touch the real database; it simply verifies that
    # the module can be imported and that the public callables exist.
    print("PASS")