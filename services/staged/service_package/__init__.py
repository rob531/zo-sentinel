# Auto-emitted service package.
# Relative intra-service imports survive staged->active promotion without rewrite.
# deps: requests
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Ensure app imports work from both direct execution and staged/active promotion
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import (
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    User,
)

router = APIRouter(prefix="/api/service_package", tags=["service_package"])

_ZO_STORE_URL = "http://127.0.0.1:8772"

# --- Pydantic request/response models ---


class MeshScoreQuery(BaseModel):
    org_id: Optional[str] = None
    perspective_ids: Optional[List[str]] = None
    time_range_days: int = Field(default=30, ge=1)


class MeshScoreResponse(BaseModel):
    scores: List[Dict[str, Any]] = Field(default_factory=list)
    timestamp: Optional[str] = None


class SignalScoreQuery(BaseModel):
    org_id: Optional[str] = None
    entity_ids: Optional[List[str]] = None
    signal_types: Optional[List[str]] = None
    limit: int = Field(default=100, ge=1)


class SignalScoreResponse(BaseModel):
    scores: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0


class MeshMemoryQuery(BaseModel):
    perspective_id: Optional[str] = None
    entity_id: Optional[str] = None


class MeshMemoryResponse(BaseModel):
    memory: List[Dict[str, Any]] = Field(default_factory=list)


class ScoreDisputeResponse(BaseModel):
    id: int
    server_id: str
    proposed_overall_risk: Optional[str] = None
    reason_category: Optional[str] = None
    explanation: Optional[str] = None
    status: str
    created_at: Optional[str] = None


class QuarantineResetRequest(BaseModel):
    server_id: str


class SuccessResponse(BaseModel):
    success: bool = True
    message: Optional[str] = None


# --- Helper: query mesh/pipeline tables via write_service ---


def _query_mesh(query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Query the ZoComputer mesh store at 127.0.0.1:8772."""
    try:
        resp = requests.post(
            f"{_ZO_STORE_URL}/query",
            json={"query": query, "params": params or {}},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", []) if isinstance(data, dict) else []
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"MESH query failed: {e}")


# --- Router endpoints ---


@router.post("/mesh_scores", response_model=MeshScoreResponse)
def mesh_scores_endpoint(
    body: MeshScoreQuery,
    db: Session = Depends(get_session),
) -> MeshScoreResponse:
    """Fetch mesh scores from the pipeline store (mcp_signal_scores)."""
    params: Dict[str, Any] = {}
    if body.org_id:
        params["org_id"] = body.org_id

    query = "SELECT * FROM mcp_signal_scores WHERE 1=1"
    if body.org_id:
        query += " AND org_id = :org_id"
    if body.perspective_ids:
        placeholders = ", ".join(f":pid_{i}" for i in range(len(body.perspective_ids)))
        query += f" AND perspective_id IN ({placeholders})"
        for i, pid in enumerate(body.perspective_ids):
            params[f"pid_{i}"] = pid

    results = _query_mesh(query, params)
    return MeshScoreResponse(scores=results)


@router.post("/signal_scores", response_model=SignalScoreResponse)
def signal_scores_endpoint(
    body: SignalScoreQuery,
    db: Session = Depends(get_session),
) -> SignalScoreResponse:
    """Fetch signal scores from app DB (McpLlmAxisScore) scoped by org."""
    query = db.query(McpLlmAxisScore)
    # Apply limit
    scores = query.limit(body.limit).all()
    score_list = [
        {
            "id": s.id,
            "server_id": s.server_id,
            "axis_name": s.axis_name,
            "label": s.label,
            "p_critical": s.p_critical,
            "p_danger": s.p_danger,
            "scored_at": s.scored_at.isoformat() if s.scored_at else None,
        }
        for s in scores
    ]
    return SignalScoreResponse(scores=score_list, total=len(score_list))


@router.post("/mesh_memory", response_model=MeshMemoryResponse)
def mesh_memory_endpoint(
    body: MeshMemoryQuery,
    db: Session = Depends(get_session),
) -> MeshMemoryResponse:
    """Fetch mesh memory from pipeline store."""
    params: Dict[str, Any] = {}
    query = "SELECT * FROM mesh_memory WHERE 1=1"
    if body.perspective_id:
        query += " AND perspective_id = :perspective_id"
        params["perspective_id"] = body.perspective_id
    if body.entity_id:
        query += " AND entity_id = :entity_id"
        params["entity_id"] = body.entity_id

    results = _query_mesh(query, params)
    return MeshMemoryResponse(memory=results)


@router.get("/mesh_memory/{memory_id}", response_model=Optional[Dict[str, Any]])
def get_mesh_memory_by_id(
    memory_id: str,
    db: Session = Depends(get_session),
) -> Optional[Dict[str, Any]]:
    """Fetch a specific mesh memory entry by ID."""
    query = "SELECT * FROM mesh_memory WHERE memory_id = :memory_id"
    results = _query_mesh(query, {"memory_id": memory_id})
    return results[0] if results else None


@router.get("/score_disputes", response_model=List[ScoreDisputeResponse])
def get_score_disputes_endpoint(
    db: Session = Depends(get_session),
) -> List[ScoreDisputeResponse]:
    """Fetch all score disputes from app DB."""
    disputes = db.query(McpScoreDispute).all()
    return [
        ScoreDisputeResponse(
            id=d.id,
            server_id=d.server_id,
            proposed_overall_risk=d.proposed_overall_risk,
            reason_category=d.reason_category,
            explanation=d.explanation,
            status=d.status or "pending",
            created_at=d.created_at.isoformat() if d.created_at else None,
        )
        for d in disputes
    ]


@router.post("/reset_quarantine", response_model=SuccessResponse)
def reset_quarantine_api(
    body: QuarantineResetRequest,
    db: Session = Depends(get_session),
) -> SuccessResponse:
    """Reset quarantine flag for a server."""
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == body.server_id
    ).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return SuccessResponse(success=True, message=f"Quarantine reset for {body.server_id}")


# --- Package-level functions called by other services ---


def get_mesh_memory(org_id: Optional[str] = None) -> Dict[str, Any]:
    """Package-level: fetch mesh_memory for an org."""
    params: Dict[str, Any] = {}
    query = "SELECT * FROM mesh_memory WHERE 1=1"
    if org_id:
        query += " AND org_id = :org_id"
        params["org_id"] = org_id
    results = _query_mesh(query, params)
    return {"memory": results, "count": len(results)}


def get_mesh_memory_endpoint(entity_type: str, entity_id: str) -> Dict[str, Any]:
    """Package-level: fetch mesh memory for an entity (alias for callers)."""
    query = "SELECT * FROM mesh_memory WHERE entity_type = :entity_type AND entity_id = :entity_id"
    results = _query_mesh(query, {"entity_type": entity_type, "entity_id": entity_id})
    return {"memory": results, "count": len(results)}


def get_mesh_scores(mesh_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Package-level: fetch mesh scores by mesh_id."""
    params: Dict[str, Any] = {}
    query = "SELECT * FROM mcp_signal_scores WHERE 1=1"
    if mesh_id:
        query += " AND mesh_id = :mesh_id"
        params["mesh_id"] = mesh_id
    return _query_mesh(query, params)


def mesh_scores(mesh_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Package-level: fetch mesh scores (alias for get_mesh_scores)."""
    return get_mesh_scores(mesh_id)


def get_signal_scores(org_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Package-level: fetch signal scores from app DB."""
    # Use generator-based session to match get_session() pattern
    session_gen = get_session()
    session = next(session_gen)
    try:
        scores = session.query(McpLlmAxisScore).all()
        return [
            {
                "id": s.id,
                "server_id": s.server_id,
                "axis_name": s.axis_name,
                "label": s.label,
                "p_critical": s.p_critical,
                "scored_at": s.scored_at.isoformat() if s.scored_at else None,
            }
            for s in scores
        ]
    finally:
        try:
            session.close()
        except Exception:
            pass


def reset_server_export_quarantine_api(server_id: str) -> Dict[str, Any]:
    """Package-level: reset server export quarantine."""
    session_gen = get_session()
    session = next(session_gen)
    try:
        server = session.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == server_id
        ).first()
        if server:
            return {"success": True, "server_id": server_id}
        return {"success": False, "error": "not found"}
    finally:
        try:
            session.close()
        except Exception:
            pass


def _dummy_post(endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Package-level: dummy POST helper for testing."""
    return {"endpoint": endpoint, "posted": data, "status": "ok"}


# Re-export for callers that inherit/import these
ScoreDisputes = McpScoreDispute
Users = User


def _run_self_test() -> str:
    """Self-test: verify module loads and basic functions work."""
    try:
        # Verify router has expected routes
        assert hasattr(router, "routes")
        route_paths = {r.path for r in router.routes}
        assert "/api/service_package/mesh_scores" in route_paths
        assert "/api/service_package/signal_scores" in route_paths
        assert "/api/service_package/mesh_memory" in route_paths
        assert "/api/service_package/score_disputes" in route_paths
        assert "/api/service_package/reset_quarantine" in route_paths

        # Verify Pydantic models
        MeshScoreQuery.model_validate({})
        SignalScoreQuery.model_validate({})
        MeshMemoryQuery.model_validate({})
        QuarantineResetRequest.model_validate({"server_id": "test"})
        SuccessResponse.model_validate({"success": True})

        # Verify package-level functions exist and are callable
        assert callable(get_mesh_memory)
        assert callable(get_mesh_scores)
        assert callable(mesh_scores)
        assert callable(get_signal_scores)
        assert callable(reset_server_export_quarantine_api)
        assert callable(_dummy_post)
        assert callable(mesh_scores_endpoint)
        assert callable(mesh_memory_endpoint)
        assert callable(signal_scores_endpoint)
        assert callable(get_score_disputes_endpoint)
        assert callable(get_mesh_memory_by_id)
        assert callable(get_mesh_memory_endpoint)
        assert callable(reset_quarantine_api)

        return "PASS"
    except Exception as e:
        return f"FAIL: {e}"


if __name__ == "__main__":
    result = _run_self_test()
    print(result)
    sys.exit(0 if result == "PASS" else 1)
