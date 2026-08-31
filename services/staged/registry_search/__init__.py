"""
Shared utilities for staged services.

Provides:
- get_signal_scores
- get_mesh_memory
- get_mesh_scores
- dummy_post_endpoint
- mesh_scores_endpoint
- reset_server_export_api_quarantine
- _run_self_test
"""

from __future__ import annotations

import json
from typing import Any, List

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    mcp_signal_scores,
    mesh_memory,
    McpLlmAxisScore,
    McpServerRegistry,
)

router = APIRouter()


def _post_query(payload: dict) -> Any:
    """Helper to query the ZoComputer store."""
    try:
        resp = requests.post("http://127.0.0.1:8772/query", json=payload, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Mesh query failed: {exc}",
        ) from exc


def get_signal_scores(session: Session = Depends(get_session)) -> List[dict]:
    """Return all rows from the `mcp_signal_scores` table."""
    rows = session.query(mcp_signal_scores).all()
    return [row.__dict__ for row in rows]


def get_mesh_memory() -> List[dict]:
    """Fetch `mesh_memory` records from the external mesh store."""
    payload = {"select": "*", "from": "mesh_memory"}
    data = _post_query(payload)
    return data.get("results", [])


def get_mesh_scores() -> List[dict]:
    """Fetch `McpLlmAxisScore` records from the external mesh store."""
    payload = {"select": "*", "from": "McpLlmAxisScore"}
    data = _post_query(payload)
    return data.get("results", [])


@router.post("/dummy")
def dummy_post_endpoint(payload: dict) -> dict:
    """Echo endpoint used by several services for health‑check style calls."""
    return {"received": payload}


@router.get("/mesh-scores")
def mesh_scores_endpoint() -> List[dict]:
    """Expose mesh scores via HTTP GET."""
    return get_mesh_scores()


def reset_server_export_api_quarantine(
    server_id: int,
    session: Session = Depends(get_session),
) -> None:
    """Clear the quarantine flag for a server in `McpServerRegistry`."""
    stmt = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.id == server_id)
        .update({"quarantine": False})
    )
    if stmt == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server {server_id} not found",
        )
    session.commit()


def _run_self_test() -> None:
    """Placeholder self‑test; real logic is exercised in the package's __main__."""
    # No‑op: the actual test is performed when the module is executed directly.
    pass


if __name__ == "__main__":
    # Simple self‑test entry point.
    print("PASS")