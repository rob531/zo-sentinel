"""
Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite.
"""
from typing import Optional
from datetime import datetime, timedelta
import httpx

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute


router = APIRouter(prefix="/risk-tier-summary", tags=["risk-tier-summary"])


class MeshScore(BaseModel):
    server_id: int
    server_name: str
    risk_tier: int
    score: float


class MeshScoresResponse(BaseModel):
    mesh_scores: list[MeshScore]
    total: int


def get_mesh_scores(
    days: int = 30,
    risk_tier: Optional[int] = None,
    min_score: Optional[float] = None,
    limit: int = 100,
) -> list[MeshScore]:
    """
    Fetch mesh scores from the ZoComputer store.
    Called by mcp_risk_tier_distribution_by_org and other services.
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    
    where_clauses = [f"scored_at >= '{cutoff}'"]
    if risk_tier is not None:
        where_clauses.append(f"risk_tier = {risk_tier}")
    if min_score is not None:
        where_clauses.append(f"score >= {min_score}")
    
    where_sql = " AND ".join(where_clauses)
    
    sql = f"""
    SELECT server_id, server_name, risk_tier, score
    FROM mcp_signal_scores
    WHERE {where_sql}
    ORDER BY score DESC
    LIMIT {limit}
    """
    
    payload = {"sql": sql, "limit": limit}
    
    with httpx.Client(timeout=30.0) as client:
        resp = client.post("http://127.0.0.1:8772/query", json=payload)
        resp.raise_for_status()
        data = resp.json()
    
    results = data.get("results", []) if isinstance(data, dict) else data
    return [
        MeshScore(
            server_id=row.get("server_id"),
            server_name=row.get("server_name", ""),
            risk_tier=row.get("risk_tier", 0),
            score=float(row.get("score", 0.0)),
        )
        for row in results
    ]


@router.get("/mesh-scores", response_model=MeshScoresResponse)
def mesh_scores_endpoint(
    days: int = Query(default=30, ge=1, le=365),
    risk_tier: Optional[int] = Query(default=None, ge=0, le=4),
    min_score: Optional[float] = Query(default=None, ge=0.0, le=100.0),
    limit: int = Query(default=100, ge=1, le=1000),
):
    """
    Retrieve mesh scores with optional filtering by risk tier and minimum score.
    """
    scores = get_mesh_scores(
        days=days,
        risk_tier=risk_tier,
        min_score=min_score,
        limit=limit,
    )
    return MeshScoresResponse(mesh_scores=scores, total=len(scores))


@router.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "mcp_risk_tier_summary"}


def _run_self_test():
    """Self-test verifies the service compiles and its functions are callable."""
    from fastapi import FastAPI
    from app.db import get_session
    
    app = FastAPI()
    app.include_router(router)
    
    # Verify routes are registered
    routes = [r.path for r in app.routes]
    assert "/risk-tier-summary/mesh-scores" in routes, "mesh-scores endpoint missing"
    assert "/risk-tier-summary/health" in routes, "health endpoint missing"
    
    # Verify get_mesh_scores is callable
    assert callable(get_mesh_scores), "get_mesh_scores must be callable"
    
    print("PASS")


if __name__ == "__main__":
    _run_self_test()