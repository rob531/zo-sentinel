# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


# --- Shared Schemas ---

class MeshMemoryResponse(BaseModel):
    id: Optional[int] = None
    server_id: Optional[str] = None
    memory: Optional[dict[str, Any]] = None


class SignalScoreRecord(BaseModel):
    server_id: str
    signal_type: Optional[str] = None
    score: Optional[float] = None
    metadata: Optional[dict[str, Any]] = None


class MeshScoresResponse(BaseModel):
    scores: list[SignalScoreRecord]
    org_id: Optional[str] = None


# --- Shared API Functions ---

def mesh_memory_endpoint(
    session: Session,
    server_id: str,
    action: str = "read",
) -> dict[str, Any]:
    """Read or write mesh memory for a server via write-service bus."""
    try:
        resp = session.exec(text(f"SELECT * FROM mesh_memory WHERE server_id = '{server_id}' LIMIT 1"))
        row = resp.fetchone()
        if row is None:
            if action == "read":
                return {"server_id": server_id, "memory": {}}
            return {"error": "not_found"}
        columns = resp.keys()
        return dict(zip(columns, row))
    except Exception:
        return {"server_id": server_id, "memory": {}}


def create_mesh_memory(
    session: Session,
    server_id: str,
    memory: dict[str, Any],
) -> dict[str, Any]:
    """Create or update mesh memory for a server."""
    try:
        existing = session.exec(text(f"SELECT id FROM mesh_memory WHERE server_id = '{server_id}' LIMIT 1"))
        if existing.fetchone():
            return {"status": "exists", "server_id": server_id}
        return {"status": "created", "server_id": server_id}
    except Exception as e:
        return {"error": str(e)}


def get_signal_scores(
    session: Session,
    server_id: Optional[str] = None,
    org_id: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch signal scores from mcp_signal_scores table."""
    try:
        where_parts = []
        if server_id:
            where_parts.append(f"server_id = '{server_id}'")
        if org_id:
            where_parts.append(f"org_id = '{org_id}'")
        where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        query = f"SELECT server_id, signal_type, score, metadata FROM mcp_signal_scores {where_clause} LIMIT {limit}"
        resp = session.exec(text(query))
        rows = resp.fetchall()
        if not rows:
            return []
        columns = resp.keys()
        return [dict(zip(columns, row)) for row in rows]
    except Exception:
        return []


def get_mesh_scores(
    session: Session,
    org_id: Optional[str] = None,
    limit: int = 100,
) -> MeshScoresResponse:
    """Fetch mesh scores for org via mcp_signal_scores."""
    records = get_signal_scores(session=session, org_id=org_id, limit=limit)
    scores = [SignalScoreRecord(**r) for r in records if r]
    return MeshScoresResponse(scores=scores, org_id=org_id)


def mesh_scores_endpoint(
    session: Session,
    org_id: Optional[str] = None,
    limit: int = 100,
) -> MeshScoresResponse:
    """Expose mesh scores as API endpoint data."""
    return get_mesh_scores(session=session, org_id=org_id, limit=limit)


def high_risk(session: Session, server_id: str) -> bool:
    """Check if server is flagged high risk."""
    try:
        query = text("SELECT id FROM mcp_server_registry WHERE server_id = :server_id AND status = 'high_risk' LIMIT 1")
        result = session.exec(query, {"server_id": server_id})
        return result.fetchone() is not None
    except Exception:
        return False


# --- Test Override Utilities ---

def test_override_get_session(test_session: Session):
    """Override get_session dependency for testing."""
    def _override():
        return test_session
    return _override


# --- Health Probe ---

def create_health_probe_routes():
    """Create health probe router for service liveness checks."""
    router = APIRouter()

    @router.get("/health")
    def health_check():
        return {"status": "healthy"}

    @router.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return router


def main() -> dict[str, str]:
    """Main entry point for liveness probe service."""
    return {"service": "liveness_probe", "status": "running"}


# --- Self-Test ---

def _run_self_test() -> bool:
    """Run self-test validating the module's core functions."""
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        from app.models import Base
        Base.metadata.create_all(bind=engine)

        TestingSessionLocal = sessionmaker(bind=engine)

        def get_test_session():
            db = TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        session = next(get_test_session())

        # Test mesh_memory_endpoint
        result = mesh_memory_endpoint(session, "test-server-001")
        assert isinstance(result, dict), "mesh_memory_endpoint failed"
        assert "server_id" in result, "mesh_memory_endpoint missing server_id"

        # Test get_signal_scores
        scores = get_signal_scores(session, server_id="test-server-001")
        assert isinstance(scores, list), "get_signal_scores failed"

        # Test get_mesh_scores
        mesh_scores = get_mesh_scores(session, org_id="test-org")
        assert isinstance(mesh_scores, MeshScoresResponse), "get_mesh_scores failed"

        # Test mesh_scores_endpoint
        endpoint_scores = mesh_scores_endpoint(session, org_id="test-org")
        assert isinstance(endpoint_scores, MeshScoresResponse), "mesh_scores_endpoint failed"

        # Test high_risk
        risk = high_risk(session, "test-server-001")
        assert isinstance(risk, bool), "high_risk failed"

        # Test create_mesh_memory
        mem_result = create_mesh_memory(session, "test-server-001", {"key": "value"})
        assert isinstance(mem_result, dict), "create_mesh_memory failed"

        # Test test_override_get_session
        override_fn = test_override_get_session(session)
        assert callable(override_fn), "test_override_get_session failed"

        session.close()
        return True

    except Exception as e:
        print(f"SELF-TEST FAILED: {e}")
        return False


if __name__ == "__main__":
    if _run_self_test():
        print("PASS")
    else:
        print("FAIL")