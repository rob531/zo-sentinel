"""Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite.
"""

from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute

router = APIRouter()


class MeshMemoryResponse(BaseModel):
    id: str
    content: str
    metadata: Optional[Dict[str, Any]] = None


class MeshScoreResponse(BaseModel):
    id: str
    score: float
    signal_type: str
    metadata: Optional[Dict[str, Any]] = None


class ScoreDisputeResponse(BaseModel):
    id: UUID
    score_id: UUID
    dispute_reason: str
    status: str


def _query_mesh_store(query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Query the ZoComputer MESH/pipeline store."""
    import requests
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query, "params": params or {}},
        timeout=30
    )
    response.raise_for_status()
    return response.json().get("results", [])


@router.get("/mesh/memory/{memory_id}", response_model=MeshMemoryResponse)
def mesh_memory_endpoint(memory_id: str) -> MeshMemoryResponse:
    """Retrieve mesh memory by ID from the MESH store."""
    results = _query_mesh_store(
        "SELECT * FROM mesh_memory WHERE id = %s",
        {"id": memory_id}
    )
    if not results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Mesh memory {memory_id} not found"
        )
    row = results[0]
    return MeshMemoryResponse(
        id=str(row.get("id")),
        content=row.get("content", ""),
        metadata=row.get("metadata")
    )


@router.get("/mesh/scores/{score_id}", response_model=MeshScoreResponse)
def mesh_scores_endpoint(score_id: str) -> MeshScoreResponse:
    """Retrieve mesh score by ID from the MESH store."""
    results = _query_mesh_store(
        "SELECT * FROM mcp_signal_scores WHERE id = %s",
        {"id": score_id}
    )
    if not results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Mesh score {score_id} not found"
        )
    row = results[0]
    return MeshScoreResponse(
        id=str(row.get("id")),
        score=float(row.get("score", 0.0)),
        signal_type=row.get("signal_type", ""),
        metadata=row.get("metadata")
    )


def get_signal_score_by_id(score_id: str) -> Optional[Dict[str, Any]]:
    """Get a signal score by ID (internal helper)."""
    results = _query_mesh_store(
        "SELECT * FROM mcp_signal_scores WHERE id = %s",
        {"id": score_id}
    )
    return results[0] if results else None


def get_mesh_memory_by_id(memory_id: str) -> Optional[Dict[str, Any]]:
    """Get mesh memory by ID (internal helper)."""
    results = _query_mesh_store(
        "SELECT * FROM mesh_memory WHERE id = %s",
        {"id": memory_id}
    )
    return results[0] if results else None


def delete_score_dispute(dispute_id: UUID, db: Session = Depends(get_session)) -> bool:
    """Delete a score dispute from the app database."""
    result = db.execute(
        delete(McpScoreDispute).where(McpScoreDispute.id == dispute_id)
    )
    db.commit()
    return result.rowcount > 0


class HealthResponse(BaseModel):
    status: str


@router.get("/health", response_model=HealthResponse)
def service_health() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(status="healthy")


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200, f"Health check failed: {response.status_code}"
    assert response.json()["status"] == "healthy", f"Unexpected health status: {response.json()}"

    print("PASS")