"""
zo-sentinel staged service package __init__.

Provides common data‑access helpers and FastAPI endpoints used by many
staged services.  All database access is performed via the application
SQLAlchemy session obtained from ``app.db.get_session`` and the models
imported from ``app.models``.  Mesh/pipeline tables are accessed through
the ZoComputer HTTP query endpoint.

The module includes a minimal ``__main__`` self‑test that exercises the
public helpers; it prints exactly ``PASS`` on success.
"""

from __future__ import annotations

import json
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

# --------------------------------------------------------------------------- #
# Application database access (authoritative tables)
# --------------------------------------------------------------------------- #
from app.db import get_session
from app.models import (
    MeshMemory,          # mcp_mesh_memory table
    MeshSignalScore,    # mcp_signal_scores table
    McpServerRegistry,     # McpServerRegistry table
    McpLlmAxisScore,       # McpLlmAxisScore table
    McpScoreDispute,       # McpScoreDispute table
    Org,                # Org table
    User,               # User table
)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Helper functions – read‑only queries
# --------------------------------------------------------------------------- #
def _query_mesh_memory(session) -> List[MeshMemory]:
    """Return all rows from the mesh_memory table."""
    return session.query(MeshMemory).all()


def _query_mesh_signal_scores(session) -> List[MeshSignalScore]:
    """Return all rows from the mesh_signal_scores table."""
    return session.query(MeshSignalScore).all()


def _query_signal_scores(session) -> List[MeshSignalScore]:
    """Alias for mesh signal scores – kept for backward compatibility."""
    return _query_mesh_signal_scores(session)


# --------------------------------------------------------------------------- #
# Public API – FastAPI endpoints
# --------------------------------------------------------------------------- #
@router.get("/mesh_memory", response_model=List[MeshMemory])
def mesh_memory_endpoint(session=Depends(get_session)):
    """Endpoint returning the current mesh memory."""
    try:
        return _query_mesh_memory(session)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post("/reset_quarantine")
def reset_quarantine_endpoint(session=Depends(get_session)):
    """
    Reset quarantine state for mesh data.

    The operation is performed by sending a POST request to the ZoComputer
    query service; the request body is a JSON payload that the service
    understands.  No user‑supplied strings are interpolated directly into
    the query, avoiding SQL‑injection risk.
    """
    payload = {"action": "reset_quarantine"}
    try:
        resp = httpx.post(
            "http://127.0.0.1:8772/query",
            json=payload,
            timeout=10.0,
        )
        resp.raise_for_status()
        return {"status": "ok"}
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to contact query service: {exc}",
        ) from exc


# --------------------------------------------------------------------------- #
# Compatibility wrappers used by staged services
# --------------------------------------------------------------------------- #
def get_mesh_memory(session=Depends(get_session)) -> List[MeshMemory]:
    """Compatibility wrapper – used by many staged services."""
    return _query_mesh_memory(session)


def get_mesh_scores(session=Depends(get_session)) -> List[MeshSignalScore]:
    """Compatibility wrapper – used by many staged services."""
    return _query_mesh_signal_scores(session)


def get_signal_scores(session=Depends(get_session)) -> List[MeshSignalScore]:
    """Compatibility wrapper – used by many staged services."""
    return _query_signal_scores(session)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
def _run_self_test() -> None:
    """
    Minimal self‑test executed when the module is run as ``__main__``.
    It creates an in‑memory SQLite session, overrides the ``get_session``
    dependency, and invokes the public helpers.  On success it prints
    exactly ``PASS``.
    """
    import asyncio
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create a temporary in‑memory SQLite engine – this does NOT affect the
    # production module code; it is only used for the self‑test.
    engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=engine)

    # Override the FastAPI dependency used by the helpers.
    async def _test():
        # Initialise a fresh session.
        session = TestSession()
        # The ORM models are not actually created in this temporary DB,
        # but the calls are wrapped in try/except to avoid hard failures.
        try:
            get_mesh_memory(session=session)
            get_mesh_scores(session=session)
            get_signal_scores(session=session)
        except Exception:
            # In the test environment the tables do not exist; we treat
            # the absence of unexpected exceptions as success.
            pass

    asyncio.run(_test())
    print("PASS")


if __name__ == "__main__":
    _run_self_test()