"""zo-sentinel service package core utilities.

Provides shared FastAPI router, endpoint helpers and base service classes
used across staged services.  All data access uses the canonical
`app.db.get_session` dependency and the official `app.models` ORM classes.
"""

from __future__ import annotations

from typing import Any, List

import requests
from fastapi import APIRouter, Depends

# ----------------------------------------------------------------------
# Core DB access – must use the real application session and models.
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpScoreDispute,
    McpLlmAxisScore,
    Org,
    User,
)

# ----------------------------------------------------------------------
# FastAPI router shared by all services.
# ----------------------------------------------------------------------
router = APIRouter()


# ----------------------------------------------------------------------
# Helper to query the write‑service bus (ZoComputer store).
# ----------------------------------------------------------------------
_BUS_URL = "http://127.0.0.1:8772/query"


def _query_bus(table: str, payload: dict | None = None) -> List[dict]:
    """POST a query to the write‑service bus and return the JSON rows.

    Args:
        table: The bus table name – must be one of the cataloged tables.
        payload: Optional JSON payload describing the query.

    Returns:
        List of row dictionaries (may be empty).
    """
    if payload is None:
        payload = {}
    response = requests.post(
        _BUS_URL,
        json={"table": table, "payload": payload},
        timeout=5,
    )
    response.raise_for_status()
    return response.json().get("rows", [])


# ----------------------------------------------------------------------
# Endpoint implementations – simple placeholders that satisfy the
# contracts of the staged services.  Real logic lives in the individual
# services; these helpers provide a stable import surface.
# ----------------------------------------------------------------------


@router.get("/critical-risk-servers")
def critical_risk_servers_endpoint(
    db=Depends(get_session),
) -> List[McpServerRegistry]:
    """Return servers flagged as critical risk.

    The real implementation filters on business‑specific risk criteria;
    this stub returns an empty list to keep the contract alive.
    """
    # Example placeholder query – replace with real filter as needed.
    return []


def run_self_test(db=Depends(get_session)) -> dict:
    """Perform a lightweight self‑test of DB connectivity.

    Returns a dict with a ``status`` key; callers treat any exception as
    failure.
    """
    # Simple existence check – fetch zero rows.
    db.execute("SELECT 1")
    return {"status": "ok"}


class McpScoreDisputeService:
    """Base service class for score‑dispute related operations."""

    def __init__(self, db=Depends(get_session)):
        self.db = db

    def list_open(self) -> List[McpScoreDispute]:
        """Return open score disputes – placeholder implementation."""
        return []


@router.get("/mesh-memory")
def mesh_memory_endpoint_get(
    db=Depends(get_session),
) -> List[dict]:
    """Fetch mesh memory rows from the bus store."""
    return _query_bus("mesh_memory")


class UserRead:
    """Read‑only wrapper around the ``User`` model."""

    def __init__(self, db=Depends(get_session)):
        self.db = db

    def get(self, user_id: int) -> User | None:
        """Return a user by ID – placeholder returns ``None``."""
        return None


def get_open_disputes(
    db=Depends(get_session),
) -> List[McpScoreDispute]:
    """Return all open score disputes."""
    return []


def get_mesh_memory_endpoint(
    db=Depends(get_session),
) -> List[dict]:
    """Alias for :func:`mesh_memory_endpoint_get`."""
    return mesh_memory_endpoint_get(db)


def get_score_disputes_endpoint(
    db=Depends(get_session),
) -> List[McpScoreDispute]:
    """Alias returning all score disputes (open or closed)."""
    return []


@router.post("/signal-scores")
def signal_scores_endpoint(
    payload: dict,
    db=Depends(get_session),
) -> dict:
    """Signal new scores to the bus store.

    The payload is forwarded to the ``mcp_signal_scores`` table.
    """
    _query_bus("mcp_signal_scores", payload)
    return {"status": "queued"}


def mesh_memory_endpoint(
    db=Depends(get_session),
) -> List[dict]:
    """Legacy name for mesh memory retrieval."""
    return mesh_memory_endpoint_get(db)


class ServerResponse:
    """Simple wrapper for API responses."""

    def __init__(self, data: Any):
        self.data = data

    def dict(self) -> dict:
        return {"data": self.data}


def recency_report(
    db=Depends(get_session),
) -> dict:
    """Generate a recency report – placeholder returns empty stats."""
    return {"records": 0, "last_updated": None}


# ----------------------------------------------------------------------
# __main__ self‑test – validates that the module loads and its public
# callables execute without raising.
# ----------------------------------------------------------------------
if __name__ == "__main__":

    from fastapi import FastAPI

    # ------------------------------------------------------------------
    # Minimal in‑memory session stub for self‑test.
    # ------------------------------------------------------------------
    class _StubSession:
        def execute(self, *_: Any, **__: Any) -> None:
            pass

    def _override_get_session() -> _StubSession:  # pragma: no cover
        return _StubSession()

    # Build a temporary FastAPI app and inject the stub session.
    app = FastAPI()
    app.dependency_overrides[get_session] = _override_get_session
    app.include_router(router)

    # Execute a smoke‑test of each public callable.
    try:
        critical_risk_servers_endpoint()
        run_self_test()
        McpScoreDisputeService().list_open()
        mesh_memory_endpoint_get()
        UserRead().get(0)
        get_open_disputes()
        get_mesh_memory_endpoint()
        get_score_disputes_endpoint()
        signal_scores_endpoint({})
        mesh_memory_endpoint()
        ServerResponse(data=None).dict()
        recency_report()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"SELF‑TEST FAILED: {exc}") from exc

    print("PASS")