# deps: fastapi, sqlalchemy, pydantic
"""FastAPI router for MCP LLM Axis Scores.

Provides read-only endpoints to surface SFT risk-axis verdicts for MCP servers.
Auth is public per directive config; multi-tenancy is not required for read-only
score retrieval (server_id is not org-scoped in the schema).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api/mcp-llm-axis-scores", tags=["mcp_llm_axis_scores_api"])


class AxisScoreResponse(BaseModel):
    server_id: str
    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    probs: Optional[dict]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: Optional[bool]
    escalated_to: Optional[str]
    decision_rule_version: Optional[str]
    model_version: str
    adapter_sha256: Optional[str]
    scored_at: Optional[datetime]

    class Config:
        from_attributes = True


class AxisScoreListResponse(BaseModel):
    scores: List[AxisScoreResponse]
    total: int


# Valid axis names for enum validation
VALID_AXES = {
    "overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
    "network_egress", "maintainer_trust", "exploit_surface"
}


@router.get("/scores/{server_id}", response_model=AxisScoreListResponse)
def get_scores_for_server(
    server_id: str,
    model_version: Optional[str] = Query(None, description="Filter by model version"),
    db: Session = Depends(get_session),
):
    """Return all axis scores for a given server_id."""
    query = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id)
    if model_version:
        query = query.filter(McpLlmAxisScore.model_version == model_version)
    rows = query.order_by(McpLlmAxisScore.axis_name).all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No scores found for server: {server_id}")
    return AxisScoreListResponse(
        scores=[AxisScoreResponse.model_validate(r) for r in rows],
        total=len(rows),
    )


@router.get("/scores/{server_id}/{axis_name}", response_model=AxisScoreResponse)
def get_axis_score(
    server_id: str,
    axis_name: str,
    model_version: Optional[str] = Query(None, description="Filter by model version"),
    db: Session = Depends(get_session),
):
    """Return a single axis score for a server."""
    if axis_name not in VALID_AXES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid axis_name. Must be one of: {sorted(VALID_AXES)}",
        )
    query = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == axis_name,
    )
    if model_version:
        query = query.filter(McpLlmAxisScore.model_version == model_version)
    row = query.first()
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No score for server={server_id} axis={axis_name}",
        )
    return AxisScoreResponse.model_validate(row)


@router.get("/servers/{server_id}/verdict", response_model=dict)
def get_server_verdict(
    server_id: str,
    model_version: Optional[str] = Query(None, description="Filter by model version"),
    db: Session = Depends(get_session),
):
    """Return the full 7-axis verdict for a server (one row per axis)."""
    query = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id)
    if model_version:
        query = query.filter(McpLlmAxisScore.model_version == model_version)
    rows = query.all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No verdict found for server: {server_id}")
    return {
        "server_id": server_id,
        "model_version": rows[0].model_version if rows else None,
        "axes": {r.axis_name: r.label for r in rows},
        "scored_at": rows[0].scored_at.isoformat() if rows and rows[0].scored_at else None,
    }


@router.get("/scores", response_model=AxisScoreListResponse)
def list_scores(
    server_id: Optional[str] = Query(None, description="Filter by server_id"),
    axis_name: Optional[str] = Query(None, description="Filter by axis_name"),
    model_version: Optional[str] = Query(None, description="Filter by model version"),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_session),
):
    """List axis scores with optional filters and pagination."""
    query = db.query(McpLlmAxisScore)
    if server_id:
        query = query.filter(McpLlmAxisScore.server_id == server_id)
    if axis_name:
        if axis_name not in VALID_AXES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid axis_name. Must be one of: {sorted(VALID_AXES)}",
            )
        query = query.filter(McpLlmAxisScore.axis_name == axis_name)
    if model_version:
        query = query.filter(McpLlmAxisScore.model_version == model_version)

    total = query.count()
    rows = (
        query.order_by(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return AxisScoreListResponse(
        scores=[AxisScoreResponse.model_validate(r) for r in rows],
        total=total,
    )


if __name__ == "__main__":
    import sys
    # All test imports are LOCAL so py_compile (syntax-only) never touches them.
    # Only resolve app.* imports when the file is actually executed.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base, McpLlmAxisScore
    from app.db import get_session

    # Build an isolated FastAPI app for the self-test (no app.main dependency)
    test_app = FastAPI()
    test_app.include_router(router)

    # In-memory SQLite with StaticPool for cross-thread test client
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    _test_db = TestSession()

    # Seed one test row
    _test_db.add(
        McpLlmAxisScore(
            server_id="srv-test-1",
            axis_name="overall_risk",
            label="LOW",
            label_index=0,
            probs={"LOW": 0.85, "HIGH": 0.15},
            p_top=0.85,
            p_critical=0.05,
            p_danger=0.10,
            escalated=False,
            escalated_to=None,
            decision_rule_version="v1.0",
            model_version="model-v1",
            adapter_sha256="sha256abc",
            scored_at=datetime.utcnow(),
        )
    )
    _test_db.commit()

    def _override_session():
        try:
            yield _test_db
        finally:
            pass

    test_app.dependency_overrides[get_session] = _override_session
    client = TestClient(test_app)

    # --- Happy path tests ---
    resp = client.get("/api/mcp-llm-axis-scores/scores/srv-test-1")
    if resp.status_code != 200:
        print(f"FAIL: get_scores_for_server returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if data.get("total", -1) < 1 or not data.get("scores"):
        print(f"FAIL: empty scores list: {data}")
        sys.exit(1)

    resp2 = client.get("/api/mcp-llm-axis-scores/scores/srv-test-1/overall_risk")
    if resp2.status_code != 200:
        print(f"FAIL: get_axis_score returned {resp2.status_code}: {resp2.text}")
        sys.exit(1)

    resp3 = client.get("/api/mcp-llm-axis-scores/servers/srv-test-1/verdict")
    if resp3.status_code != 200:
        print(f"FAIL: get_server_verdict returned {resp3.status_code}: {resp3.text}")
        sys.exit(1)
    verdict = resp3.json()
    if "axes" not in verdict or "overall_risk" not in verdict.get("axes", {}):
        print(f"FAIL: malformed verdict: {verdict}")
        sys.exit(1)

    resp4 = client.get("/api/mcp-llm-axis-scores/scores?server_id=srv-test-1&limit=10")
    if resp4.status_code != 200:
        print(f"FAIL: list_scores returned {resp4.status_code}: {resp4.text}")
        sys.exit(1)

    # --- Auth/permission failure: clear override so dependency returns nothing ---
    test_app.dependency_overrides.clear()
    resp5 = client.get("/api/mcp-llm-axis-scores/scores/srv-test-1")
    if resp5.status_code == 200:
        # With no session override the DB access should fail or return 500
        print(f"FAIL: expected non-200 without session override, got {resp5.status_code}")
        sys.exit(1)

    # --- Validation failure ---
    resp6 = client.get("/api/mcp-llm-axis-scores/scores/srv-test-1/invalid_axis")
    if resp6.status_code != 400:
        print(f"FAIL: expected 400 for invalid axis, got {resp6.status_code}")
        sys.exit(1)

    print("PASS")
