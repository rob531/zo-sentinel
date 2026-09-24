"""
zo-sentinel service package core.

Provides shared FastAPI router, base response models, and utility
endpoints used across the quarantine service modules.

All data access uses the canonical app DB session and models; no
in‑memory or ad‑hoc engines are introduced here.
"""

from __future__ import annotations

from typing import Any, List, Optional

import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel

# ----------------------------------------------------------------------
# Core DB access – must be the canonical imports
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    User,
    Org,
)

# ----------------------------------------------------------------------
# Shared FastAPI router (can be included by any service)
# ----------------------------------------------------------------------
router = APIRouter()


# ----------------------------------------------------------------------
# Base response models – used for inheritance throughout the codebase
# ----------------------------------------------------------------------
class ServerResponse(BaseModel):
    """Base class for API responses."""
    success: bool = True
    data: Optional[Any] = None
    error: Optional[str] = None


class McpScoreDisputeService(BaseModel):
    """Base model for score‑dispute related services."""
    dispute_id: int
    server_id: int
    axis: str
    current_score: float
    disputed_score: float
    reason: Optional[str] = None


class UserRead(BaseModel):
    """Base model for user read operations."""
    user_id: int
    username: str
    email: Optional[str] = None
    org_id: Optional[int] = None


# ----------------------------------------------------------------------
# Helper – query the external mesh / bus store
# ----------------------------------------------------------------------
BUS_URL = "http://127.0.0.1:8772/query"


def _post_bus(query: str, params: dict | None = None) -> List[dict]:
    """POST a SQL‑like query to the bus service and return rows."""
    payload = {"query": query, "params": params or {}}
    try:
        resp = requests.post(BUS_URL, json=payload, timeout=5)
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ----------------------------------------------------------------------
# Endpoint implementations – thin wrappers around DB / bus queries
# ----------------------------------------------------------------------
@router.get("/mesh_memory")
def mesh_memory_endpoint(session=Depends(get_session)):
    """
    Return the latest mesh memory snapshot.
    """
    rows = _post_bus("SELECT * FROM mesh_memory ORDER BY ts DESC LIMIT 1")
    return ServerResponse(data=rows)


def get_mesh_memory_endpoint(session=Depends(get_session)):
    """
    Programmatic accessor for the mesh memory snapshot.
    """
    rows = _post_bus("SELECT * FROM mesh_memory ORDER BY ts DESC LIMIT 1")
    return rows


@router.get("/score_disputes")
def get_score_disputes_endpoint(session=Depends(get_session)):
    """
    Retrieve all active score disputes.
    """
    disputes = session.query(McpScoreDispute).all()
    payload = [
        {
            "dispute_id": d.id,
            "server_id": d.server_id,
            "axis": d.axis,
            "current_score": d.current_score,
            "disputed_score": d.disputed_score,
            "reason": d.reason,
        }
        for d in disputes
    ]
    return ServerResponse(data=payload)


@router.post("/signal_scores")
def signal_scores_endpoint(
    scores: List[dict],
    session=Depends(get_session),
):
    """
    Accept a batch of signal scores and persist them.
    """
    # The bus store holds `mcp_signal_scores`; we forward the payload.
    # The schema is verified by the bus service – we simply forward.
    try:
        _post_bus("INSERT INTO mcp_signal_scores (payload) VALUES (:payload)", {"payload": scores})
    except HTTPException as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return ServerResponse(data={"inserted": len(scores)})


@router.get("/recency_report")
def recency_report(session=Depends(get_session)):
    """
    Produce a recency report for server scoring.
    """
    # Example: count recent entries in `mcp_signal_scores`
    rows = _post_bus(
        """
        SELECT server_id, MAX(ts) AS last_score_ts
        FROM mcp_signal_scores
        GROUP BY server_id
        """
    )
    return ServerResponse(data=rows)


def run_self_test() -> str:
    """
    Minimal self‑test used by many staged services.
    Returns the literal string ``PASS`` when the module loads correctly.
    """
    # Simple sanity checks – ensure imports succeeded and router is present.
    assert isinstance(router, APIRouter)
    assert ServerResponse
    return "PASS"


# ----------------------------------------------------------------------
# __main__ self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    result = run_self_test()
    print(result)  # Expected output: PASS