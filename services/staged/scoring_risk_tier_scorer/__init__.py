"""Shared utilities for staged services."""
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import text
from typing import Optional, List, Dict, Any

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute


class SignalScoresResponse(BaseModel):
    scores: List[Dict[str, Any]]
    total: int


class MeshMemoryResponse(BaseModel):
    memories: List[Dict[str, Any]]


def get_mesh_memory(session, server_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch mesh_memory entries, optionally filtered by server_id."""
    query = "SELECT * FROM mesh_memory"
    params = {}
    if server_id:
        query += " WHERE server_id = :server_id"
        params["server_id"] = server_id
    result = session.execute(text(query), params)
    return [dict(row._mapping) for row in result]


def get_mesh_memory_by_id(session, memory_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single mesh_memory entry by id."""
    result = session.execute(
        text("SELECT * FROM mesh_memory WHERE id = :id"),
        {"id": memory_id}
    )
    row = result.first()
    return dict(row._mapping) if row else None


def get_signal_scores(session, server_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch mcp_signal_scores entries."""
    query = "SELECT * FROM mcp_signal_scores"
    params = {}
    if server_id:
        query += " WHERE server_id = :server_id"
        params["server_id"] = server_id
    result = session.execute(text(query), params)
    return [dict(row._mapping) for row in result]


def signal_scores_endpoint(session, server_id: Optional[str] = None) -> SignalScoresResponse:
    """Endpoint handler for signal scores."""
    scores = get_signal_scores(session, server_id)
    return SignalScoresResponse(scores=scores, total=len(scores))


def llm_axis_scores_endpoint(session, server_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch LLM axis scores from mcp_llm_axis_scores table."""
    query = "SELECT * FROM mcp_llm_axis_scores"
    params = {}
    if server_id:
        query += " WHERE server_id = :server_id"
        params["server_id"] = server_id
    result = session.execute(text(query), params)
    return [dict(row._mapping) for row in result]


def mesh_memory_endpoint(session, server_id: Optional[str] = None) -> MeshMemoryResponse:
    """Endpoint handler for mesh memory."""
    memories = get_mesh_memory(session, server_id)
    return MeshMemoryResponse(memories=memories)


def get_mesh_memory_endpoint(session, server_id: Optional[str] = None) -> MeshMemoryResponse:
    """Alias for mesh_memory_endpoint."""
    return mesh_memory_endpoint(session, server_id)


def self_test_endpoint(session) -> Dict[str, str]:
    """Basic connectivity self-test."""
    result = session.execute(text("SELECT 1 as ok"))
    row = result.first()
    return {"status": "ok" if row.ok == 1 else "fail"}


def _run_self_test(session) -> bool:
    """Run self-test logic."""
    try:
        result = session.execute(text("SELECT 1 as ok"))
        return result.first().ok == 1
    except Exception:
        return False


def run_self_test(session) -> Dict[str, Any]:
    """Public self-test runner."""
    passed = _run_self_test(session)
    return {"test": "connectivity", "passed": passed}


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In-memory self-test setup
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(engine)

    test_session = TestSession()

    # Run tests
    results = []
    try:
        r1 = _run_self_test(test_session)
        results.append(("self_test", r1))
    except Exception as e:
        results.append(("self_test", False))

    all_passed = all(r[1] for r in results)
    print("PASS" if all_passed else "FAIL")