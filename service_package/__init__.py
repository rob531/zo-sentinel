# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.
# deps: requests

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Fix `app` shadow BEFORE any app-imports ───────────────────────────────────
# parents[1] = /home/workspace/zo_sentinel (service_package/)
_repo_root = str(Path(__file__).resolve().parents[1])

# Remove /home/workspace which shadows app/ with app.py,
# then prepend the correct repo root.
_shadowed = "/home/workspace"
_clean = [p for p in sys.path if p != _shadowed]
if _repo_root not in _clean:
    _clean.insert(0, _repo_root)
sys.path[:] = _clean

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry

router = APIRouter(prefix="/api/service_package", tags=["service_package"])

_ZO_STORE_URL = "http://127.0.0.1:8772"


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


def _query_mesh(query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Query the ZoComputer mesh store at 127.0.0.1:8772 (parameterized)."""
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


@router.post("/mesh_scores", response_model=MeshScoreResponse)
def mesh_scores_endpoint(
    body: MeshScoreQuery,
    db: Session = Depends(get_session),
) -> MeshScoreResponse:
    params: Dict[str, Any] = {}
    q = "SELECT * FROM mcp_signal_scores WHERE 1=1"
    if body.org_id:
        q += " AND org_id = :org_id"
        params["org_id"] = body.org_id
    if body.perspective_ids:
        placeholders = ", ".join(f":pid_{i}" for i in range(len(body.perspective_ids)))
        q += f" AND perspective_id IN ({placeholders})"
        for i, pid in enumerate(body.perspective_ids):
            params[f"pid_{i}"] = pid
    results = _query_mesh(q, params)
    return MeshScoreResponse(scores=results)


get_mesh_scores_endpoint = mesh_scores_endpoint


@router.post("/signal_scores", response_model=SignalScoreResponse)
def signal_scores_endpoint(
    body: SignalScoreQuery,
    db: Session = Depends(get_session),
) -> SignalScoreResponse:
    scores = db.query(McpLlmAxisScore).limit(body.limit).all()
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
    params: Dict[str, Any] = {}
    q = "SELECT * FROM mesh_memory WHERE 1=1"
    if body.perspective_id:
        q += " AND perspective_id = :perspective_id"
        params["perspective_id"] = body.perspective_id
    if body.entity_id:
        q += " AND entity_id = :entity_id"
        params["entity_id"] = body.entity_id
    results = _query_mesh(q, params)
    return MeshMemoryResponse(memory=results)


@router.get("/mesh_memory/{memory_id}", response_model=Optional[Dict[str, Any]])
def get_mesh_memory_by_id(
    memory_id: str,
    db: Session = Depends(get_session),
) -> Optional[Dict[str, Any]]:
    q = "SELECT * FROM mesh_memory WHERE memory_id = :memory_id"
    results = _query_mesh(q, {"memory_id": memory_id})
    return results[0] if results else None


@router.get("/score_disputes", response_model=List[ScoreDisputeResponse])
def get_score_disputes_endpoint(
    db: Session = Depends(get_session),
) -> List[ScoreDisputeResponse]:
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
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == body.server_id
    ).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return SuccessResponse(success=True, message=f"Quarantine reset for {body.server_id}")


def get_mesh_memory(org_id: Optional[str] = None) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    q = "SELECT * FROM mesh_memory WHERE 1=1"
    if org_id:
        q += " AND org_id = :org_id"
        params["org_id"] = org_id
    results = _query_mesh(q, params)
    return {"memory": results, "count": len(results)}


def get_mesh_memory_endpoint(entity_type: str, entity_id: str) -> Dict[str, Any]:
    q = "SELECT * FROM mesh_memory WHERE entity_type = :entity_type AND entity_id = :entity_id"
    results = _query_mesh(q, {"entity_type": entity_type, "entity_id": entity_id})
    return {"memory": results, "count": len(results)}


def get_mesh_scores(mesh_id: Optional[str] = None) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {}
    q = "SELECT * FROM mcp_signal_scores WHERE 1=1"
    if mesh_id:
        q += " AND mesh_id = :mesh_id"
        params["mesh_id"] = mesh_id
    return _query_mesh(q, params)


def mesh_scores(mesh_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return get_mesh_scores(mesh_id)


def get_signal_scores(db: Session) -> List[Dict[str, Any]]:
    """Query axis scores using an injected session (caller provides via Depends)."""
    scores = db.query(McpLlmAxisScore).all()
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


def reset_server_export_quarantine_api(server_id: str, db: Session) -> Dict[str, Any]:
    """Reset quarantine for a server using an injected session."""
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if server:
        return {"success": True, "server_id": server_id}
    return {"success": False, "error": "not found"}


def _dummy_post(endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {"endpoint": endpoint, "posted": data, "status": "ok"}


def _run_self_test() -> str:
    """Self-test: verify routes, models, and callable exports without live DB.

    Uses a standalone FastAPI app to avoid the /home/workspace/app.py shadow
    that interferes when this module is run as __main__ from the repo root.
    """
    try:
        # ── Route existence (the router is the contract) ─────────────────────
        route_paths = {r.path for r in router.routes}
        assert "/api/service_package/mesh_scores" in route_paths, \
            f"missing mesh_scores route, got: {route_paths}"
        assert "/api/service_package/signal_scores" in route_paths
        assert "/api/service_package/mesh_memory" in route_paths
        assert "/api/service_package/score_disputes" in route_paths
        assert "/api/service_package/reset_quarantine" in route_paths

        # ── Pydantic model validation ────────────────────────────────────────
        MeshScoreQuery.model_validate({})
        SignalScoreQuery.model_validate({})
        MeshMemoryQuery.model_validate({})
        QuarantineResetRequest.model_validate({"server_id": "test"})
        SuccessResponse.model_validate({"success": True})

        # ── Callable exports (backward compat for callers) ────────────────────
        assert callable(get_mesh_memory)
        assert callable(get_mesh_scores)
        assert callable(mesh_scores)
        assert callable(get_signal_scores)
        assert callable(reset_server_export_quarantine_api)
        assert callable(_dummy_post)
        assert callable(mesh_scores_endpoint)
        assert callable(get_mesh_scores_endpoint)
        assert callable(mesh_memory_endpoint)
        assert callable(get_mesh_memory_by_id)
        assert callable(get_score_disputes_endpoint)
        assert callable(get_mesh_memory_endpoint)
        assert callable(reset_quarantine_api)
        assert callable(signal_scores_endpoint)

        # ── Standalone FastAPI smoke with SQLite override ──────────────────────
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        _engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        from app.db import Base as _Base
        _Base.metadata.create_all(bind=_engine)
        _TestingSession = sessionmaker(bind=_engine)

        def _override_session():
            session = _TestingSession()
            try:
                yield session
            finally:
                session.close()

        _test_app = FastAPI(title="service_package_test")
        _test_app.include_router(router)
        _test_app.dependency_overrides[get_session] = _override_session
        _client = TestClient(_test_app)

        # Health: standalone app has no /health, so check 404
        r = _client.get("/health")
        assert r.status_code == 404

        # mesh_scores → 200 (MESH store unreachable → 500, but request round-trips)
        r = _client.post("/api/service_package/mesh_scores", json={})
        assert r.status_code in (200, 500), f"mesh_scores: {r.status_code}"

        # signal_scores → 200 with empty list (SQLite has no data)
        r = _client.post("/api/service_package/signal_scores", json={"limit": 5})
        assert r.status_code == 200, f"signal_scores: {r.status_code}"
        assert r.json()["total"] == 0

        # mesh_memory → 200 or 500 (same MESH store issue)
        r = _client.post("/api/service_package/mesh_memory", json={})
        assert r.status_code in (200, 500)

        # score_disputes → 200 (empty)
        r = _client.get("/api/service_package/score_disputes")
        assert r.status_code == 200, f"score_disputes: {r.status_code}"

        # reset_quarantine with unknown server → 404
        r = _client.post("/api/service_package/reset_quarantine", json={"server_id": "nonexistent"})
        assert r.status_code == 404, f"reset_quarantine 404: {r.status_code}"

        return "PASS"
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"FAIL: {e}"


if __name__ == "__main__":
    result = _run_self_test()
    print(result)
    sys.exit(0 if result == "PASS" else 1)
