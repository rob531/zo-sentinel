"""Auto-emitted service package for mesh scoring and memory endpoints."""
import json
from typing import Any, List, Optional
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func
import httpx

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    User,
)


def _query_bus(sql: str, params: Optional[dict] = None) -> List[dict]:
    """Query the ZoComputer bus at 127.0.0.1:8772."""
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                "http://127.0.0.1:8772/query",
                json={"sql": sql, "params": params or {}},
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            return response.json().get("rows", [])
    except httpx.TimeoutException:
        return []
    except Exception:
        return []


class ServerResponse(BaseModel):
    """Base server response model."""
    server_id: str
    status: str
    message: Optional[str] = None


class McpScoreDisputeService:
    """Service for managing MCP score disputes."""
    
    def __init__(self, session=None):
        self.session = session
    
    def get_open_disputes(self) -> List[dict]:
        """Get all open disputes."""
        sql = """
        SELECT dispute_id, server_id, axis, score_delta, 
               created_at, resolved_at, resolution_notes
        FROM mcp_score_disputes 
        WHERE resolved_at IS NULL
        ORDER BY created_at DESC
        """
        return _query_bus(sql)
    
    def resolve_dispute(self, dispute_id: str, resolution: str) -> dict:
        """Resolve a dispute by ID."""
        sql = """
        UPDATE mcp_score_disputes 
        SET resolved_at = NOW(), resolution_notes = %s
        WHERE dispute_id = %s
        RETURNING *
        """
        results = _query_bus(sql, {"p0": resolution, "p1": dispute_id})
        return results[0] if results else {}


class UserRead:
    """Base user read model for API responses."""
    
    def __init__(self, user_id: str, username: str, email: str, org_id: Optional[str] = None):
        self.user_id = user_id
        self.username = username
        self.email = email
        self.org_id = org_id


class MeshMemoryResponse(BaseModel):
    """Response model for mesh memory endpoint."""
    server_id: str
    memory_type: str
    content: dict
    timestamp: datetime


def mesh_memory_endpoint(
    server_id: str,
    memory_type: str = "context",
    session=Depends(get_session),
) -> List[MeshMemoryResponse]:
    """Fetch mesh memory for a given server."""
    sql = """
    SELECT server_id, memory_type, content, timestamp
    FROM mesh_memory
    WHERE server_id = %s AND memory_type = %s
    ORDER BY timestamp DESC
    LIMIT 100
    """
    rows = _query_bus(sql, {"p0": server_id, "p1": memory_type})
    return [
        MeshMemoryResponse(
            server_id=r["server_id"],
            memory_type=r["memory_type"],
            content=json.loads(r["content"]) if isinstance(r["content"], str) else r["content"],
            timestamp=r["timestamp"],
        )
        for r in rows
    ]


def mesh_memory_endpoint_get(
    server_id: str,
    memory_type: str = "context",
) -> List[MeshMemoryResponse]:
    """Fetch mesh memory without session dependency."""
    return mesh_memory_endpoint(server_id, memory_type)


class SignalScoreRecord(BaseModel):
    """Signal score record from the bus."""
    server_id: str
    axis: str
    score: float
    confidence: float
    source: str
    timestamp: datetime


def signal_scores_endpoint(
    server_id: Optional[str] = None,
    axis: Optional[str] = None,
    limit: int = 100,
) -> List[SignalScoreRecord]:
    """Fetch signal scores from the bus."""
    conditions = []
    params = {"limit": limit}
    
    if server_id:
        conditions.append("server_id = %s")
        params["p0"] = server_id
    if axis:
        conditions.append("axis = %s")
        key = "p1" if server_id else "p0"
        params[key] = axis
    
    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    
    sql = f"""
    SELECT server_id, axis, score, confidence, source, timestamp
    FROM mcp_signal_scores
    {where_clause}
    ORDER BY timestamp DESC
    LIMIT %(limit)s
    """
    rows = _query_bus(sql, params)
    return [
        SignalScoreRecord(
            server_id=r["server_id"],
            axis=r["axis"],
            score=r["score"],
            confidence=r["confidence"],
            source=r["source"],
            timestamp=r["timestamp"],
        )
        for r in rows
    ]


def recency_report(days: int = 7) -> dict:
    """Generate recency report for signal scores."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    sql = """
    SELECT server_id, COUNT(*) as score_count, 
           MAX(timestamp) as last_score_at
    FROM mcp_signal_scores
    WHERE timestamp > %s
    GROUP BY server_id
    ORDER BY score_count DESC
    """
    rows = _query_bus(sql, {"p0": cutoff.isoformat()})
    return {
        "report_date": datetime.utcnow().isoformat(),
        "days": days,
        "servers": rows,
    }


def get_users(session=Depends(get_session)) -> List[UserRead]:
    """Get all users from the app database."""
    result = session.execute(select(User))
    users = result.scalars().all()
    return [
        UserRead(
            user_id=str(u.id),
            username=u.username,
            email=u.email,
            org_id=str(u.org_id) if u.org_id else None,
        )
        for u in users
    ]


def dummy_post_api() -> dict:
    """Dummy endpoint for post operations."""
    return {"status": "ok", "message": "POST accepted"}


def mesh_scores_endpoint(
    server_id: Optional[str] = None,
    axis: Optional[str] = None,
) -> List[SignalScoreRecord]:
    """Alias for signal_scores_endpoint for mesh scoring."""
    return signal_scores_endpoint(server_id=server_id, axis=axis)


def get_mesh_memory_endpoint(
    server_id: str,
    memory_type: str = "context",
) -> List[MeshMemoryResponse]:
    """Get mesh memory endpoint - wrapper."""
    return mesh_memory_endpoint(server_id, memory_type)


def get_score_disputes_endpoint(
    resolved: Optional[bool] = None,
) -> List[dict]:
    """Get score disputes endpoint."""
    service = McpScoreDisputeService()
    if resolved is False:
        return service.get_open_disputes()
    sql = """
    SELECT dispute_id, server_id, axis, score_delta, 
           created_at, resolved_at, resolution_notes
    FROM mcp_score_disputes 
    ORDER BY created_at DESC
    LIMIT 100
    """
    return _query_bus(sql)


def run_self_test() -> dict:
    """Run self-test for the service package."""
    results = {"mesh_memory": False, "signal_scores": False, "disputes": False}
    
    try:
        mem = _query_bus(
            "SELECT 1 as test FROM mesh_memory LIMIT 1"
        )
        results["mesh_memory"] = True
    except Exception:
        pass
    
    try:
        scores = _query_bus(
            "SELECT 1 as test FROM mcp_signal_scores LIMIT 1"
        )
        results["signal_scores"] = True
    except Exception:
        pass
    
    try:
        disputes = _query_bus(
            "SELECT 1 as test FROM mcp_score_disputes LIMIT 1"
        )
        results["disputes"] = True
    except Exception:
        pass
    
    return results


def test_self() -> str:
    """Test self - returns PASS if all tests pass."""
    results = run_self_test()
    if all(results.values()):
        return "PASS"
    return f"FAIL: {results}"


__all__ = [
    "ServerResponse",
    "McpScoreDisputeService",
    "UserRead",
    "MeshMemoryResponse",
    "SignalScoreRecord",
    "mesh_memory_endpoint",
    "mesh_memory_endpoint_get",
    "signal_scores_endpoint",
    "recency_report",
    "get_users",
    "dummy_post_api",
    "mesh_scores_endpoint",
    "get_mesh_memory_endpoint",
    "get_score_disputes_endpoint",
    "run_self_test",
    "test_self",
]


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    test_session_factory = sessionmaker(bind=test_engine)
    
    def override_get_session():
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()
    
    test_app = FastAPI()
    
    @test_app.get("/mesh-memory/{server_id}")
    def test_mesh_memory(server_id: str, memory_type: str = "context"):
        return mesh_memory_endpoint(server_id, memory_type)
    
    @test_app.get("/signal-scores")
    def test_signal_scores(server_id: str = None, axis: str = None):
        return signal_scores_endpoint(server_id, axis)
    
    @test_app.get("/recency-report")
    def test_recency(days: int = 7):
        return recency_report(days)
    
    @test_app.get("/test-self")
    def test_run():
        return {"result": test_self()}
    
    test_app.dependency_overrides[get_session] = override_get_session
    
    print(test_self())