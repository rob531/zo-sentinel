"""Common utilities for auto‑emitted services.

Provides:
- DB session dependency (`get_db`)
- Generic POST query helper (`_post_query`)
- Mesh memory accessor (`get_mesh_memory`)
- Mesh scores endpoint (`mesh_scores_endpoint`)
- Signal scores endpoint (`signal_scores_endpoint`)
- Org lookup (`get_org_by_id`)
- Base registry class (`MCPServiceRegistry`)
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# App DB imports – must be used exactly as the application defines them.
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
)

# ----------------------------------------------------------------------
# Dependency helpers
# ----------------------------------------------------------------------


def get_db() -> Session:
    """FastAPI dependency that yields a DB session."""
    return get_session()


# ----------------------------------------------------------------------
# External write‑service query helper
# ----------------------------------------------------------------------


def _post_query(table: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    Send a POST request to the ZoComputer write‑service.

    Args:
        table: The target table name (must be in the bus catalog).
        payload: Optional JSON payload for the query.

    Returns:
        Parsed JSON response from the service.

    Raises:
        httpx.HTTPError: If the request fails.
    """
    url = "http://127.0.0.1:8772/query"
    body = {"table": table, "payload": payload or {}}
    response = httpx.post(url, json=body, timeout=10.0)
    response.raise_for_status()
    return response.json()


# ----------------------------------------------------------------------
# Mesh memory utilities
# ----------------------------------------------------------------------


def get_mesh_memory() -> Dict[str, Any]:
    """Retrieve the current mesh memory from the write‑service."""
    return _post_query("mesh_memory")


# ----------------------------------------------------------------------
# Mesh scores endpoint (used by several services)
# ----------------------------------------------------------------------


def mesh_scores_endpoint() -> List[Dict[str, Any]]:
    """
    FastAPI endpoint returning all rows from ``mcp_signal_scores``.
    """
    result = _post_query("mcp_signal_scores")
    return result.get("rows", [])


# ----------------------------------------------------------------------
# Signal scores endpoint (used by staged consumers)
# ----------------------------------------------------------------------


def signal_scores_endpoint() -> List[Dict[str, Any]]:
    """
    FastAPI endpoint returning all rows from ``mcp_signal_scores``.
    """
    result = _post_query("mcp_signal_scores")
    return result.get("rows", [])


# ----------------------------------------------------------------------
# Org lookup helper
# ----------------------------------------------------------------------


def get_org_by_id(org_id: int, db: Session = Depends(get_db)) -> Org:
    """
    Retrieve an organisation record by its primary key.

    Args:
        org_id: Primary key of the organisation.
        db: DB session (injected by FastAPI).

    Returns:
        The matching ``Org`` instance.

    Raises:
        HTTPException: If the organisation does not exist.
    """
    org = db.query(Org).filter(Org.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return org


# ----------------------------------------------------------------------
# Base registry class for admin dispute services
# ----------------------------------------------------------------------


class MCPServiceRegistry(McpServerRegistry):
    """Base class for service‑registry records used by admin dispute services."""
    pass


# ----------------------------------------------------------------------
# Optional FastAPI app for manual testing
# ----------------------------------------------------------------------


def _create_app() -> FastAPI:
    app = FastAPI()

    @app.get("/mesh_memory")
    def mesh_memory_route():
        return get_mesh_memory()

    @app.get("/mesh_scores")
    def mesh_scores_route():
        return mesh_scores_endpoint()

    @app.get("/signal_scores")
    def signal_scores_route():
        return signal_scores_endpoint()

    return app


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Replace the network call with a deterministic stub for the test.
    def _stub_post_query(table: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return {"rows": [], "result": []}

    globals()["_post_query"] = _stub_post_query

    try:
        # Exercise a representative function.
        _ = get_mesh_memory()
        print("PASS")
    except Exception as exc:  # pragma: no cover
        print(f"FAIL: {exc}")
        raise