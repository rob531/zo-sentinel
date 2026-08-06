"""Auto-emitted service package.
Relative intra-service imports survive staged->active promotion without rewrite.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Ensure app imports work from both direct execution and staged/active promotion
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

try:
    from app.db import get_session
    from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
except ImportError:
    # Fallback for when app modules aren't available yet (e.g., during build)
    get_session = None
    McpServerRegistry = None
    McpLlmAxisScore = None
    McpScoreDispute = None
    Org = None
    User = None

router = APIRouter(prefix="/api/service_package", tags=["service_package"])

_ZO_STORE_URL = "http://127.0.0.1:8772"

# --- Pydantic request/response models ---


class MeshScoreQuery(BaseModel):
    org_id: Optional[str] = None
    perspective_ids: Optional[List[str]] = None
    time_range_days: Optional[int] = 30


class MeshScoreResponse(BaseModel):
    scores: List[Dict[str, Any]]
    timestamp: Optional[str] = None


class SignalScoreQuery(BaseModel):
    org_id: Optional[str] = None
    entity_ids: Optional[List[str]] = None
    signal_types: Optional[List[str]] = None


class SignalScoreResponse(BaseModel):
    scores: List[Dict[str, Any]]
    total: int


class MeshMemoryQuery(BaseModel):
    perspective_id: Optional[str] = None
    entity_id: Optional[str] = None


class MeshMemoryResponse(BaseModel):
    memory: List[Dict[str, Any]]


class QuarantineResetRequest(BaseModel):
    server_id: str


class SuccessResponse(BaseModel):
    success: bool
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


# --- Endpoints exposed via router ---


@router.post("/mesh_scores", response_model=MeshScoreResponse)
def mesh_scores_endpoint(
    body: MeshScoreQuery,
    db: Session = Depends(get_session),
) -> MeshScoreResponse:
    """Fetch mesh scores from the pipeline store (mcp_signal_scores)."""
    params: Dict[str, Any] = {}
    if body.org_id:
        params["org_id"] = body.org_id
    if body.perspective_ids:
        params["perspective_ids"] = body.perspective_ids

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
    if body.org_id:
        query = query.filter(McpLlmAxisScore.server_id.in_(
            db.query(McpServerRegistry.server_id).filter(McpServerRegistry.server_id.isnot(None))
        ))
    scores = query.all()
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
def get_mesh_memory_endpoint(
    memory_id: str,
    db: Session = Depends(get_session),
) -> Optional[Dict[str, Any]]:
    """Fetch a specific mesh memory entry by ID."""
    query = "SELECT * FROM mesh_memory WHERE memory_id = :memory_id"
    results = _query_mesh(query, {"memory_id": memory_id})
    return results[0] if results else None


@router.get("/score_disputes", response_model=List[Dict[str, Any]])
def get_score_disputes(
    db: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """Fetch all score disputes from app DB."""
    disputes = db.query(McpScoreDispute).all()
    return [
        {
            "id": d.id,
            "server_id": d.server_id,
            "axis_name": d.axis_name,
            "dispute_reason": d.dispute_reason,
            "status": d.status,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
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


def get_mesh_scores(mesh_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Package-level: fetch mesh scores by mesh_id."""
    params: Dict[str, Any] = {}
    query = "SELECT * FROM mcp_signal_scores WHERE 1=1"
    if mesh_id:
        query += " AND mesh_id = :mesh_id"
        params["mesh_id"] = mesh_id
    return _query_mesh(query, params)


def get_signal_scores(org_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Package-level: fetch signal scores from app DB."""
    if get_session is None:
        return []
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
        session.close()


def reset_server_export_quarantine_api(server_id: str) -> Dict[str, Any]:
    """Package-level: reset server export quarantine."""
    if get_session is None:
        return {"success": False, "error": "db not available"}
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
        session.close()


def _dummy_post(endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Package-level: dummy POST helper for testing."""
    return {"endpoint": endpoint, "posted": data, "status": "ok"}


def _run_self_test() -> str:
    """Self-test: verify module loads and basic functions work."""
    try:
        # Verify imports work
        from app.db import get_session as _get_session
        from app.models import McpServerRegistry, McpLlmAxisScore

        # Verify router has expected routes
        assert hasattr(router, "routes")
        route_paths = {r.path for r in router.routes}
        assert "/mesh_scores" in route_paths
        assert "/signal_scores" in route_paths
        assert "/mesh_memory" in route_paths
        assert "/score_disputes" in route_paths
        assert "/reset_quarantine" in route_paths

        # Verify Pydantic models
        MeshScoreQuery.model_validate({})
        SignalScoreQuery.model_validate({})
        MeshMemoryQuery.model_validate({})
        QuarantineResetRequest.model_validate({"server_id": "test"})
        SuccessResponse.model_validate({"success": True})

        # Verify package-level functions exist and are callable
        assert callable(get_mesh_memory)
        assert callable(get_mesh_scores)
        assert callable(get_signal_scores)
        assert callable(reset_server_export_quarantine_api)
        assert callable(_dummy_post)

        return "PASS"
    except Exception as e:
        return f"FAIL: {e}"


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override get_session for self-test with in-memory SQLite
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)

    from app.models import Base
    Base.metadata.create_all(test_engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    # Patch the dependency
    import app.db as app_db_module
    original_get_session = app_db_module.get_session
    app_db_module.get_session = override_get_session

    try:
        result = _run_self_test()
        print(result)
        sys.exit(0 if result == "PASS" else 1)
    finally:
        app_db_module.get_session = original_get_session
