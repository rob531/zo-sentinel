"""Auto-emitted service package for staged services."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute


class Users(BaseModel):
    """User data model."""
    id: int
    username: str
    email: Optional[str] = None
    org_id: Optional[int] = None


class ScoreDisputes(BaseModel):
    """Score dispute model."""
    id: int
    score_id: int
    dispute_reason: str
    status: str


def mesh_scores_endpoint(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Return mesh scores from signal scores table."""
    query = text("""
        SELECT id, axis, score, confidence, created_at 
        FROM mcp_signal_scores 
        ORDER BY created_at DESC 
        LIMIT 100
    """)
    result = session.execute(query)
    return [
        {"id": row[0], "axis": row[1], "score": row[2], "confidence": row[3], "created_at": str(row[4])}
        for row in result.fetchall()
    ]


def dummy_post_api(payload: Dict[str, Any], session: Session = Depends(get_session)) -> Dict[str, str]:
    """Process dummy post request."""
    return {"status": "received", "payload_keys": list(payload.keys())}


def get_users(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Retrieve users from database."""
    query = text("SELECT id, username, email, org_id FROM users LIMIT 100")
    result = session.execute(query)
    return [
        {"id": row[0], "username": row[1], "email": row[2], "org_id": row[3]}
        for row in result.fetchall()
    ]


def get_server_registries(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Get server registries from McpServerRegistry."""
    query = text("SELECT id, server_name, endpoint, confidence FROM mcp_server_registry LIMIT 100")
    result = session.execute(query)
    return [
        {"id": row[0], "server_name": row[1], "endpoint": row[2], "confidence": row[3]}
        for row in result.fetchall()
    ]


def get_mesh_memory_endpoint(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Get mesh memory from ZoComputer store via bus."""
    import httpx
    try:
        response = httpx.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mesh_memory", "columns": ["id", "content", "created_at"]},
            timeout=5.0
        )
        if response.status_code == 200:
            return response.json().get("results", [])
    except Exception:
        pass
    return []


def mesh_memory_endpoint_get(memory_id: Optional[int] = None, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Get mesh memory by ID from bus."""
    import httpx
    if memory_id:
        try:
            response = httpx.post(
                "http://127.0.0.1:8772/query",
                json={"table": "mesh_memory", "filters": {"id": memory_id}},
                timeout=5.0
            )
            if response.status_code == 200:
                results = response.json().get("results", [])
                return results[0] if results else {}
        except Exception:
            pass
    return {}


def run_self_test(session: Session = Depends(get_session)) -> Dict[str, str]:
    """Run self-test to verify connectivity."""
    try:
        query = text("SELECT 1")
        session.execute(query)
        return {"status": "PASS", "db": "connected"}
    except Exception as e:
        return {"status": "FAIL", "db": str(e)}


def mesh_scores(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Return mesh scores from signal scores."""
    query = text("""
        SELECT id, axis, score, confidence, created_at 
        FROM mcp_signal_scores 
        ORDER BY created_at DESC 
        LIMIT 50
    """)
    result = session.execute(query)
    return [
        {"id": row[0], "axis": row[1], "score": row[2], "confidence": row[3], "created_at": str(row[4])}
        for row in result.fetchall()
    ]


def signal_scores_endpoint(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Return signal scores endpoint data."""
    query = text("""
        SELECT id, axis, score, confidence, metadata 
        FROM mcp_signal_scores 
        WHERE confidence > :min_confidence
        ORDER BY created_at DESC
    """)
    result = session.execute(query, {"min_confidence": 0.5})
    return [
        {"id": row[0], "axis": row[1], "score": row[2], "confidence": row[3], "metadata": row[4]}
        for row in result.fetchall()
    ]


def users_endpoint(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Return users endpoint data."""
    query = text("SELECT id, username, email, org_id FROM users WHERE active = :active")
    result = session.execute(query, {"active": True})
    return [
        {"id": row[0], "username": row[1], "email": row[2], "org_id": row[3]}
        for row in result.fetchall()
    ]


if __name__ == "__main__":
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    with test_engine.connect() as conn:
        conn.execute(text("CREATE TABLE mcp_signal_scores (id INTEGER, axis TEXT, score REAL, confidence REAL, metadata TEXT, created_at TIMESTAMP)"))
        conn.execute(text("CREATE TABLE users (id INTEGER, username TEXT, email TEXT, org_id INTEGER, active INTEGER)"))
        conn.execute(text("CREATE TABLE mcp_server_registry (id INTEGER, server_name TEXT, endpoint TEXT, confidence REAL)"))
        conn.commit()
    
    from app.db import get_session
    
    def override_get_session():
        with test_engine.connect() as conn:
            yield conn
    
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    
    result = run_self_test.__wrapped__ if hasattr(run_self_test, '__wrapped__') else None
    
    from app.db import get_session as gs
    with test_engine.begin() as conn:
        result = run_self_test(conn)
        print("PASS" if result.get("status") == "PASS" else f"FAIL: {result}")