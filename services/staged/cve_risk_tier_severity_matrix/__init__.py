# Auto-emitted service package.
from __future__ import annotations

import json
from typing import Any, List, Optional
from unittest.mock import MagicMock

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    User,
)

router = APIRouter()


class MeshScore(BaseModel):
    server_id: str
    axis: str
    score: float
    timestamp: Optional[str] = None


class MeshMemory(BaseModel):
    server_id: str
    memory: dict
    updated_at: Optional[str] = None


class ScoreDispute(BaseModel):
    id: int
    server_id: str
    axis: str
    disputed_score: float
    reason: Optional[str] = None


class UserOut(BaseModel):
    id: int
    username: str
    email: Optional[str] = None


class SignalScore(BaseModel):
    signal_id: str
    server_id: str
    score: float
    metadata: Optional[dict] = None


class HealthStatus(BaseModel):
    status: str
    checks: dict


def _query_sql(session: Session, query: str, params: Optional[dict] = None) -> List[dict]:
    """Execute SQL query and return results as list of dicts."""
    result = session.execute(text(query), params or {})
    columns = result.keys()
    return [dict(zip(columns, row)) for row in result.fetchall()]


def _post_mesh_query(endpoint: str, payload: dict) -> dict:
    """Post query to mesh/pipeline service."""
    import urllib.request
    import urllib.error

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:8772/{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError:
        return {"results": [], "error": "service unavailable"}


@router.get("/mesh/scores", response_model=List[MeshScore])
def mesh_scores_endpoint(session: Session = Depends(get_session)) -> List[MeshScore]:
    """Get mesh scores from pipeline store."""
    result = _post_mesh_query("query", {
        "sql": "SELECT server_id, axis, score, updated_at as timestamp FROM mcp_signal_scores ORDER BY updated_at DESC LIMIT 100"
    })
    scores = result.get("results", [])
    return [MeshScore(**row) for row in scores]


@router.get("/mesh/memory/{server_id}", response_model=MeshMemory)
def get_mesh_memory_endpoint(
    server_id: str, session: Session = Depends(get_session)
) -> MeshMemory:
    """Get mesh memory for a server."""
    result = _post_mesh_query("query", {
        "sql": f"SELECT server_id, memory, updated_at FROM mesh_memory WHERE server_id = '{server_id}' LIMIT 1"
    })
    rows = result.get("results", [])
    if not rows:
        raise HTTPException(status_code=404, detail="Memory not found")
    return MeshMemory(**rows[0])


@router.get("/disputes", response_model=List[ScoreDispute])
def get_score_disputes_endpoint(
    server_id: Optional[str] = None, session: Session = Depends(get_session)
) -> List[ScoreDispute]:
    """Get score disputes from app db."""
    query = session.query(McpScoreDispute)
    if server_id:
        query = query.filter(McpScoreDispute.server_id == server_id)
    disputes = query.all()
    return [
        ScoreDispute(
            id=d.id,
            server_id=d.server_id,
            axis=d.axis,
            disputed_score=d.disputed_score,
            reason=getattr(d, "reason", None),
        )
        for d in disputes
    ]


@router.post("/dummy")
def dummy_post_api(payload: dict = {}) -> dict:
    """Dummy POST endpoint for testing."""
    return {"status": "ok", "received": payload}


@router.get("/users", response_model=List[UserOut])
def get_users(session: Session = Depends(get_session)) -> List[UserOut]:
    """Get all users."""
    users = session.query(User).all()
    return [UserOut(id=u.id, username=u.username, email=getattr(u, "email", None)) for u in users]


@router.get("/axis/scores", response_model=List[dict])
def get_axis_scores(session: Session = Depends(get_session)) -> List[dict]:
    """Get axis scores from app db."""
    scores = session.query(McpLlmAxisScore).limit(100).all()
    return [
        {
            "id": s.id,
            "server_id": s.server_id,
            "axis": s.axis,
            "score": s.score,
        }
        for s in scores
    ]


@router.get("/servers", response_model=List[dict])
def get_servers(session: Session = Depends(get_session)) -> List[dict]:
    """Get registered servers."""
    servers = session.query(McpServerRegistry).limit(100).all()
    return [
        {
            "id": s.id,
            "server_name": s.server_name,
            "status": getattr(s, "status", "active"),
        }
        for s in servers
    ]


@router.get("/mesh/scores/{server_id}", response_model=List[MeshScore])
def get_mesh_scores(
    server_id: str, session: Session = Depends(get_session)
) -> List[MeshScore]:
    """Get mesh scores for a specific server."""
    result = _post_mesh_query("query", {
        "sql": f"SELECT server_id, axis, score, updated_at as timestamp FROM mcp_signal_scores WHERE server_id = '{server_id}'"
    })
    scores = result.get("results", [])
    return [MeshScore(**row) for row in scores]


@router.get("/health", response_model=HealthStatus)
def health_check(session: Session = Depends(get_session)) -> HealthStatus:
    """Health check endpoint."""
    checks = {"db": "ok", "mesh": "ok"}
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        checks["db"] = "error"
    try:
        _post_mesh_query("query", {"sql": "SELECT 1"})
    except Exception:
        checks["mesh"] = "error"
    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return HealthStatus(status=overall, checks=checks)


@router.get("/mesh/memory", response_model=List[MeshMemory])
def get_mesh_memory(
    server_id: Optional[str] = None, session: Session = Depends(get_session)
) -> List[MeshMemory]:
    """Get mesh memory from pipeline store."""
    if server_id:
        sql = f"SELECT server_id, memory, updated_at FROM mesh_memory WHERE server_id = '{server_id}'"
    else:
        sql = "SELECT server_id, memory, updated_at FROM mesh_memory LIMIT 100"
    result = _post_mesh_query("query", {"sql": sql})
    return [MeshMemory(**row) for row in result.get("results", [])]


@router.get("/routes/mesh-memory", response_model=MeshMemory)
def mesh_memory_route(
    server_id: str, session: Session = Depends(get_session)
) -> MeshMemory:
    """Alternative route to get mesh memory."""
    return get_mesh_memory_endpoint(server_id, session)


@router.get("/signal/scores", response_model=List[SignalScore])
def signal_scores_endpoint(
    session: Session = Depends(get_session),
) -> List[SignalScore]:
    """Get signal scores from pipeline."""
    result = _post_mesh_query("query", {
        "sql": "SELECT signal_id, server_id, score, metadata FROM mcp_signal_scores LIMIT 100"
    })
    scores = result.get("results", [])
    return [SignalScore(**row) for row in scores]


def test_service_package(session: Session = Depends(get_session)) -> dict:
    """Test function for service package validation."""
    try:
        session.execute(text("SELECT 1"))
        return {"status": "pass"}
    except Exception as e:
        return {"status": "fail", "error": str(e)}


def mesh_scores(session: Session = Depends(get_session)) -> List[MeshScore]:
    """Get mesh scores (functional version)."""
    return mesh_scores_endpoint(session)


def test_self(session: Session = Depends(get_session)) -> dict:
    """Self-test for this module."""
    return test_service_package(session)


def _run_self_test(session: Session) -> dict:
    """Internal self-test runner."""
    return test_service_package(session)


def create_app() -> FastAPI:
    """Create FastAPI application with this router."""
    app = FastAPI(title="Auto-Emitted Service Package")
    app.include_router(router)
    return app


# Module-level app for direct usage
app = create_app()

# Export router and functions
__all__ = [
    "router",
    "app",
    "mesh_scores_endpoint",
    "get_mesh_memory_endpoint",
    "get_score_disputes_endpoint",
    "dummy_post_api",
    "get_users",
    "get_axis_scores",
    "get_servers",
    "get_mesh_scores",
    "health_check",
    "get_mesh_memory",
    "mesh_memory_route",
    "signal_scores_endpoint",
    "test_service_package",
    "test_self",
    "mesh_scores",
    "_query_sql",
    "_run_self_test",
    "_post_mesh_query",
    "create_app",
]


if __name__ == "__main__":
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In-memory self-test with dependency override
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create tables
    from app.models import Base
    Base.metadata.create_all(test_engine)

    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    # Seed minimal test data
    test_session.add(User(id=1, username="testuser"))
    test_session.add(McpServerRegistry(id=1, server_name="test-server"))
    test_session.add(McpLlmAxisScore(id=1, server_id="s1", axis="security", score=0.8))
    test_session.add(McpScoreDispute(id=1, server_id="s1", axis="security", disputed_score=0.6))
    test_session.commit()

    def override_get_session():
        yield test_session

    test_app = create_app()
    test_app.dependency_overrides[get_session] = override_get_session

    # Run health check
    from fastapi.testclient import TestClient
    client = TestClient(test_app)

    health_resp = client.get("/health")
    users_resp = client.get("/users")
    servers_resp = client.get("/servers")
    disputes_resp = client.get("/disputes")
    scores_resp = client.get("/axis/scores")

    all_pass = (
        health_resp.status_code == 200
        and users_resp.status_code == 200
        and servers_resp.status_code == 200
        and disputes_resp.status_code == 200
        and scores_resp.status_code == 200
    )

    if all_pass:
        print("PASS")
    else:
        print("FAIL")
        print(f"health: {health_resp.status_code}")
        print(f"users: {users_resp.status_code}")
        print(f"servers: {servers_resp.status_code}")
        print(f"disputes: {disputes_resp.status_code}")
        print(f"scores: {scores_resp.status_code}")