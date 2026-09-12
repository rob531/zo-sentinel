"""Auto‑emitted service package.

Provides a minimal FastAPI router with endpoints used throughout the
quarantine services.  The implementation is intentionally lightweight;
real logic is supplied by the surrounding application.  The module
includes a __main__ self‑test that prints ``PASS``.
"""

from __future__ import annotations

from typing import Any, List

import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException

# App‑level data access – must be imported exactly as in the main app.
from app.db import get_session
from app.models import (
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    User,
)

router = APIRouter()


# ----------------------------------------------------------------------
# Helper functions (used by multiple endpoints)
# ----------------------------------------------------------------------
def _fetch_from_bus(table: str, where: dict | None = None) -> List[dict]:
    """Query the write‑service bus (127.0.0.1:8772) for *table*.

    ``where`` is a simple equality filter; it is translated into a
    minimal SQL‑like JSON payload understood by the bus service.
    """
    payload: dict[str, Any] = {"table": table}
    if where:
        payload["where"] = where
    try:
        resp = requests.post("http://127.0.0.1:8772/query", json=payload, timeout=5)
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Bus query failed: {exc}") from exc


def get_signal_scores() -> List[dict]:
    """Return all rows from the ``mcp_signal_scores`` bus table."""
    return _fetch_from_bus("mcp_signal_scores")


def mesh_memory_endpoint_get() -> List[dict]:
    """Return all rows from the ``mesh_memory`` bus table."""
    return _fetch_from_bus("mesh_memory")


def get_critical_risk_servers(session=Depends(get_session)) -> List[dict]:
    """Return servers flagged as critical risk.

    The real criteria are application‑specific; this stub returns an
    empty list.
    """
    # Example placeholder query – replace with real logic as needed.
    _ = session  # keep the dependency signature
    return []


def get_mesh_memory_by_id(memory_id: int) -> dict:
    """Return a single ``mesh_memory`` row by its primary key."""
    rows = _fetch_from_bus("mesh_memory", where={"id": memory_id})
    if not rows:
        raise HTTPException(status_code=404, detail="Mesh memory not found")
    return rows[0]


# ----------------------------------------------------------------------
# Endpoint definitions
# ----------------------------------------------------------------------
@router.post("/reset-server-export")
def reset_server_export_api_quarantine_endpoint() -> dict[str, str]:
    """Placeholder reset endpoint."""
    return {"status": "reset"}


@router.get("/signal-scores")
def signal_scores_endpoint() -> List[dict]:
    """Expose signal scores via the FastAPI router."""
    return get_signal_scores()


@router.get("/mesh-memory")
def mesh_memory_endpoint() -> List[dict]:
    """Expose mesh memory rows via the FastAPI router."""
    return mesh_memory_endpoint_get()


@router.get("/critical-risk-servers")
def critical_risk_servers_endpoint(
    session=Depends(get_session),
) -> List[dict]:
    """Expose critical risk servers via the FastAPI router."""
    return get_critical_risk_servers(session)


@router.get("/mesh-memory/{memory_id}")
def mesh_memory_by_id_endpoint(memory_id: int) -> dict:
    """Expose a single mesh memory entry via the FastAPI router."""
    return get_mesh_memory_by_id(memory_id)


@router.get("/test")
def test_endpoint() -> dict[str, str]:
    """Simple health‑check endpoint."""
    return {"status": "ok"}


# ----------------------------------------------------------------------
# Exported symbols
# ----------------------------------------------------------------------
__all__ = [
    "router",
    "reset_server_export_api_quarantine_endpoint",
    "signal_scores_endpoint",
    "mesh_memory_endpoint",
    "critical_risk_servers_endpoint",
    "mesh_memory_by_id_endpoint",
    "test_endpoint",
    "get_signal_scores",
    "mesh_memory_endpoint_get",
    "get_critical_risk_servers",
    "get_mesh_memory_by_id",
]


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Minimal self‑test: instantiate a FastAPI app, include the router,
    # and verify that the app can be created without error.
    app = FastAPI()
    app.include_router(router)

    # Dependency override for the session – a dummy object sufficient for
    # the stub implementations above.
    class DummySession:
        def query(self, *args, **kwargs):
            return []

        def execute(self, *args, **kwargs):
            return []

    app.dependency_overrides[get_session] = lambda: DummySession()

    # If we reach this point, the module loads correctly.
    print("PASS")