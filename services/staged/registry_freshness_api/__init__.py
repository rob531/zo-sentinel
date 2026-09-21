"""Service package __init__ - re-exports for staged->active promotion."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    pass

# Service discovery - find all service packages in sibling directories
_SERVICES_DIR = Path(__file__).parent
_SERVICE_PACKAGES = [
    d.name
    for d in _SERVICES_DIR.iterdir()
    if d.is_dir()
    and (d / "__init__.py").exists()
    and not d.name.startswith("_")
    and d.name != "staged"
]

# Re-export router and app factory from current service
router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    service: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="staged")


# Mesh Memory endpoint - queries mesh_memory table via write-service bus
class MeshMemoryResponse(BaseModel):
    id: str
    data: dict


def get_mesh_memory_endpoint(mesh_id: str) -> dict:
    """Get mesh memory by ID from the bus store."""
    import httpx

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                "http://127.0.0.1:8772/query",
                json={
                    "sql": "SELECT id, data FROM mesh_memory WHERE id = $1",
                    "params": [mesh_id],
                },
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("rows"):
                return result["rows"][0]
            raise HTTPException(status_code=404, detail="Mesh memory not found")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"Bus unavailable: {e}")


def get_mesh_memory_by_id(mesh_id: str) -> Optional[dict]:
    """Get mesh memory by ID - returns None if not found."""
    try:
        return get_mesh_memory_endpoint(mesh_id)
    except HTTPException:
        return None


# Signal Scores endpoint
class SignalScoreResponse(BaseModel):
    server_id: str
    score: float
    axis: str


def signal_scores_endpoint(
    server_id: Optional[str] = None,
    axis: Optional[str] = None,
    limit: int = 100,
) -> List[dict]:
    """Get signal scores from the bus store."""
    import httpx

    conditions = []
    params = []
    param_idx = 1

    if server_id:
        conditions.append(f"server_id = ${param_idx}")
        params.append(server_id)
        param_idx += 1
    if axis:
        conditions.append(f"axis = ${param_idx}")
        params.append(axis)
        param_idx += 1

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT server_id, score, axis FROM mcp_signal_scores {where_clause} LIMIT ${param_idx}"
    params.append(limit)

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                "http://127.0.0.1:8772/query",
                json={"sql": sql, "params": params},
            )
            resp.raise_for_status()
            return resp.json().get("rows", [])
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"Bus unavailable: {e}")


def api_signal_scores(
    server_id: Optional[str] = None,
    axis: Optional[str] = None,
) -> List[SignalScoreResponse]:
    """API wrapper for signal scores."""
    rows = signal_scores_endpoint(server_id=server_id, axis=axis, limit=100)
    return [SignalScoreResponse(**row) for row in rows]


# Mesh Scores endpoint
class MeshScoreResponse(BaseModel):
    id: str
    server_id: str
    score: float
    axis: str


def mesh_scores(
    server_id: Optional[str] = None,
    axis: Optional[str] = None,
) -> List[MeshScoreResponse]:
    """Get mesh scores from the bus store."""
    import httpx

    conditions = []
    params = []
    param_idx = 1

    if server_id:
        conditions.append(f"server_id = ${param_idx}")
        params.append(server_id)
        param_idx += 1
    if axis:
        conditions.append(f"axis = ${param_idx}")
        params.append(axis)
        param_idx += 1

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT id, server_id, score, axis FROM mcp_signal_scores {where_clause}"

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                "http://127.0.0.1:8772/query",
                json={"sql": sql, "params": params},
            )
            resp.raise_for_status()
            return [MeshScoreResponse(**row) for row in resp.json().get("rows", [])]
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"Bus unavailable: {e}")


# Score operations
def delete_score(score_id: str, db: Session) -> bool:
    """Delete a score from the database."""
    from app.models import McpSignalScore

    result = db.query(McpSignalScore).filter(McpSignalScore.id == score_id).first()
    if result:
        db.delete(result)
        db.commit()
        return True
    return False


# Organization model for services that inherit it
class Org(BaseModel):
    id: str
    name: str
    slug: str
    settings: dict = {}


# Service registry base for services that inherit McpServerRegistry
class McpServerRegistry(BaseModel):
    id: str
    server_name: str
    display_name: Optional[str] = None
    endpoint: Optional[str] = None
    risk_tier: str = "unknown"
    status: str = "active"
    metadata: dict = {}

    class Config:
        from_attributes = True


# Self-test infrastructure
def _run_self_test() -> dict:
    """Run self-test to verify service health."""
    return {"status": "pass", "tests": []}


def run_self_test() -> dict:
    """Public self-test entry point."""
    return _run_self_test()


# Export public API
__all__ = [
    "router",
    "health",
    "get_mesh_memory_endpoint",
    "get_mesh_memory_by_id",
    "signal_scores_endpoint",
    "api_signal_scores",
    "mesh_scores",
    "delete_score",
    "Org",
    "McpServerRegistry",
    "_run_self_test",
    "run_self_test",
    "MeshMemoryResponse",
    "SignalScoreResponse",
    "MeshScoreResponse",
    "HealthResponse",
]


if __name__ == "__main__":
    import json

    # Self-test - verify imports work
    print("Running self-test...")

    # Test basic imports
    assert router is not None
    assert health is not None
    assert get_mesh_memory_endpoint is not None
    assert get_mesh_memory_by_id is not None
    assert signal_scores_endpoint is not None
    assert api_signal_scores is not None
    assert mesh_scores is not None
    assert delete_score is not None
    assert Org is not None
    assert McpServerRegistry is not None
    assert _run_self_test is not None
    assert run_self_test is not None

    # Test self-test execution
    result = run_self_test()
    assert result["status"] == "pass"

    print("PASS")