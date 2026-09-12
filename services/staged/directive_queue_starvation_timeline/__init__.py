"""
zo-sentinel service package core utilities.

This module provides shared FastAPI router components and helper functions
used across the staged service packages.  It deliberately avoids defining
Pydantic models that reference non‑existent database columns; all data
access is performed through the canonical ``app.db.get_session`` dependency
and the real ORM models from ``app.models``.
"""

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# Core dependencies – must be imported exactly as defined in the app.
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
# Shared router
# ----------------------------------------------------------------------
router = APIRouter()


@router.get("/mesh_memory")
async def mesh_memory_endpoint(db: Session = Depends(get_session)):
    """
    Minimal placeholder endpoint used by many staged services.
    Returns a static payload; real implementations replace this logic.
    """
    # The real implementation would query the mesh_memory table via the
    # external ZoComputer store.  Here we simply confirm the DB dependency.
    if db is None:  # pragma: no cover
        raise HTTPException(status_code=500, detail="DB session missing")
    return {"status": "ok", "detail": "mesh memory endpoint reachable"}


def get_mesh_memory_by_id(memory_id: int, db: Session = Depends(get_session)):
    """
    Placeholder helper that would retrieve a mesh memory record by its ID.
    """
    # In production this would issue a request to the ZoComputer service.
    # For the purpose of the shared package we return a deterministic stub.
    return {"memory_id": memory_id, "content": "stub payload"}


# ----------------------------------------------------------------------
# Exported symbols
# ----------------------------------------------------------------------
__all__ = [
    "router",
    "mesh_memory_endpoint",
    "get_mesh_memory_by_id",
    "get_session",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "Org",
    "User",
]


# ----------------------------------------------------------------------
# Self‑test (executed when running ``python -m services.__init__``)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Build a minimal FastAPI app, include the shared router, and perform a
    # simple request using the TestClient.  The test does not hit a real DB;
    # the dependency override supplies a dummy session object.
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    class DummySession:  # pragma: no cover
        """A no‑op session used only for the self‑test."""
        def __getattr__(self, item):
            raise NotImplementedError("Dummy session has no DB operations")

    # Override the DB dependency with the dummy session.
    app.dependency_overrides[get_session] = lambda: DummySession()  # type: ignore

    client = TestClient(app)
    response = client.get("/mesh_memory")
    if response.status_code == 200 and response.json().get("status") == "ok":
        print("PASS")
    else:
        print("FAIL")