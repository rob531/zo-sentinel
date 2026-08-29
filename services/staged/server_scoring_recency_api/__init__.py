# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.
"""
server_scoring_recency_api - Service for querying recency of server scoring data.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from pydantic import BaseModel

from app.db import get_session
from app.models import User, McpServerRegistry, McpLlmAxisScore

router = APIRouter()


class RecencyScore(BaseModel):
    server_id: str
    last_scored_at: Optional[datetime]
    score_age_seconds: Optional[float]
    score_value: Optional[float]
    has_score: bool


class RecencyReport(BaseModel):
    total_servers: int
    scored_servers: int
    unscored_servers: int
    oldest_score: Optional[datetime]
    newest_score: Optional[datetime]
    scores: List[RecencyScore]


class MeshMemoryEndpoint(BaseModel):
    id: str
    content: Optional[str]
    metadata: Optional[Dict[str, Any]]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


class SignalScoreEndpoint(BaseModel):
    id: int
    server_id: str
    axis: str
    score: float
    created_at: datetime


class ScoreDisputeEndpoint(BaseModel):
    id: int
    server_id: str
    reason: str
    status: str
    created_at: datetime


class UserEndpoint(BaseModel):
    id: int
    email: str
    name: Optional[str]
    created_at: datetime


@router.get("/mesh-memory", response_model=List[MeshMemoryEndpoint])
async def mesh_memory_endpoint(
    server_id: Optional[str] = None,
    limit: int = 100,
    session=Depends(get_session),
):
    """
    Fetch mesh memory records from the MESH/pipeline store.
    Returns memory entries for server scoring context.
    """
    try:
        import requests
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": "SELECT id, content, metadata, created_at, updated_at FROM mesh_memory",
                "params": {}
            },
            timeout=5
        )
        if response.status_code == 200:
            results = response.json().get("results", [])
            filtered = [r for r in results if server_id is None or r.get("server_id") == server_id]
            return filtered[:limit]
    except Exception:
        pass
    return []


@router.get("/mesh-memory/{memory_id}", response_model=Optional[MeshMemoryEndpoint])
async def get_mesh_memory_by_id(
    memory_id: str,
    session=Depends(get_session),
):
    """
    Fetch a specific mesh memory record by ID.
    """
    try:
        import requests
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": "SELECT id, content, metadata, created_at, updated_at FROM mesh_memory WHERE id = :id",
                "params": {"id": memory_id}
            },
            timeout=5
        )
        if response.status_code == 200:
            results = response.json().get("results", [])
            return results[0] if results else None
    except Exception:
        pass
    return None


@router.get("/signal-scores", response_model=List[SignalScoreEndpoint])
async def signal_scores_endpoint(
    server_id: Optional[str] = None,
    axis: Optional[str] = None,
    limit: int = 100,
    session=Depends(get_session),
):
    """
    Fetch signal scores from the MESH/pipeline store.
    """
    try:
        import requests
        query = "SELECT id, server_id, axis, score, created_at FROM mcp_signal_scores WHERE 1=1"
        params = {}
        if server_id:
            query += " AND server_id = :server_id"
            params["server_id"] = server_id
        if axis:
            query += " AND axis = :axis"
            params["axis"] = axis
        query += f" ORDER BY created_at DESC LIMIT {limit}"
        
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": query, "params": params},
            timeout=5
        )
        if response.status_code == 200:
            return response.json().get("results", [])
    except Exception:
        pass
    return []


@router.get("/score-disputes", response_model=List[ScoreDisputeEndpoint])
async def get_score_disputes_endpoint(
    server_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    session=Depends(get_session),
):
    """
    Fetch score disputes from the app database.
    """
    query = "SELECT id, server_id, reason, status, created_at FROM McpScoreDispute WHERE 1=1"
    params = {}
    if server_id:
        query += " AND server_id = :server_id"
        params["server_id"] = server_id
    if status:
        query += " AND status = :status"
        params["status"] = status
    query += f" ORDER BY created_at DESC LIMIT {limit}"
    
    result = session.execute(text(query), params)
    rows = result.fetchall()
    return [
        ScoreDisputeEndpoint(
            id=row[0],
            server_id=row[1],
            reason=row[2],
            status=row[3],
            created_at=row[4]
        )
        for row in rows
    ]


@router.get("/users", response_model=List[UserEndpoint])
async def users_endpoint(
    limit: int = 100,
    session=Depends(get_session),
):
    """
    Fetch users from the app database.
    Note: Uses only valid User columns (no is_active which doesn't exist).
    """
    result = session.execute(
        text("SELECT id, email, name, created_at FROM users ORDER BY id LIMIT :limit"),
        {"limit": limit}
    )
    rows = result.fetchall()
    return [
        UserEndpoint(
            id=row[0],
            email=row[1],
            name=row[2],
            created_at=row[3]
        )
        for row in rows
    ]


@router.get("/recency-report", response_model=RecencyReport)
async def recency_report(
    hours_threshold: int = 24,
    session=Depends(get_session),
):
    """
    Generate a recency report for server scoring data.
    Shows which servers have been scored recently and which are stale.
    """
    threshold_time = datetime.utcnow() - timedelta(hours=hours_threshold)
    
    # Get all registered servers
    servers_result = session.execute(
        text("SELECT id, name FROM McpServerRegistry ORDER BY id")
    )
    servers = servers_result.fetchall()
    
    # Get scores from MESH store
    scores = []
    try:
        import requests
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": "SELECT server_id, score, created_at FROM mcp_signal_scores ORDER BY created_at DESC",
                "params": {}
            },
            timeout=5
        )
        if response.status_code == 200:
            scores = response.json().get("results", [])
    except Exception:
        scores = []
    
    # Build score lookup (most recent per server)
    latest_scores: Dict[str, Dict[str, Any]] = {}
    for s in scores:
        sid = s.get("server_id")
        if sid and sid not in latest_scores:
            latest_scores[sid] = s
    
    recency_scores = []
    now = datetime.utcnow()
    oldest_score = None
    newest_score = None
    
    for server in servers:
        server_id = server[0]
        score_data = latest_scores.get(server_id)
        
        if score_data:
            scored_at = score_data.get("created_at")
            if isinstance(scored_at, str):
                scored_at = datetime.fromisoformat(scored_at.replace("Z", "+00:00"))
            
            age_seconds = (now - scored_at).total_seconds() if scored_at else None
            has_score = True
            
            if oldest_score is None or (scored_at and scored_at < oldest_score):
                oldest_score = scored_at
            if newest_score is None or (scored_at and scored_at > newest_score):
                newest_score = scored_at
        else:
            scored_at = None
            age_seconds = None
            score_value = None
            has_score = False
        
        recency_scores.append(RecencyScore(
            server_id=server_id,
            last_scored_at=scored_at,
            score_age_seconds=age_seconds,
            score_value=score_data.get("score") if score_data else None,
            has_score=has_score
        ))
    
    scored_count = sum(1 for rs in recency_scores if rs.has_score)
    
    return RecencyReport(
        total_servers=len(recency_scores),
        scored_servers=scored_count,
        unscored_servers=len(recency_scores) - scored_count,
        oldest_score=oldest_score,
        newest_score=newest_score,
        scores=recency_scores
    )


# Export router for FastAPI inclusion
__all__ = [
    "router",
    "mesh_memory_endpoint",
    "get_mesh_memory_by_id",
    "signal_scores_endpoint",
    "get_score_disputes_endpoint",
    "users_endpoint",
    "recency_report",
    "RecencyReport",
    "RecencyScore",
    "MeshMemoryEndpoint",
    "SignalScoreEndpoint",
    "ScoreDisputeEndpoint",
    "UserEndpoint",
]


if __name__ == "__main__":
    """
    Self-test: verify the module loads and all expected exports exist.
    """
    import sys
    
    print("=== server_scoring_recency_api self-test ===")
    
    # Verify module loads
    from server_scoring_recency_api import (
        router,
        mesh_memory_endpoint,
        get_mesh_memory_by_id,
        signal_scores_endpoint,
        get_score_disputes_endpoint,
        users_endpoint,
        recency_report,
        RecencyReport,
        RecencyScore,
        MeshMemoryEndpoint,
        SignalScoreEndpoint,
        ScoreDisputeEndpoint,
        UserEndpoint,
    )
    
    # Verify router has expected routes
    route_paths = [r.path for r in router.routes]
    expected_routes = [
        "/mesh-memory",
        "/signal-scores",
        "/score-disputes",
        "/users",
        "/recency-report",
    ]
    
    all_ok = True
    for expected in expected_routes:
        if expected not in route_paths:
            print(f"FAIL: Missing route {expected}")
            all_ok = False
    
    # Verify Pydantic models
    try:
        _ = RecencyReport(
            total_servers=10,
            scored_servers=8,
            unscored_servers=2,
            oldest_score=None,
            newest_score=None,
            scores=[]
        )
        _ = RecencyScore(
            server_id="test-123",
            last_scored_at=None,
            score_age_seconds=None,
            score_value=None,
            has_score=False
        )
        _ = MeshMemoryEndpoint(id="mem-1")
        _ = SignalScoreEndpoint(id=1, server_id="s1", axis="x", score=0.5, created_at=datetime.utcnow())
        _ = ScoreDisputeEndpoint(id=1, server_id="s1", reason="bad", status="open", created_at=datetime.utcnow())
        _ = UserEndpoint(id=1, email="test@test.com", name="Test", created_at=datetime.utcnow())
    except Exception as e:
        print(f"FAIL: Model instantiation error: {e}")
        all_ok = False
    
    if all_ok:
        print("PASS: All exports verified, routes registered, models valid")
        sys.exit(0)
    else:
        print("FAIL: One or more checks failed")
        sys.exit(1)