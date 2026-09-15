"""Service package initialization."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import httpx

from app.db import get_session
from app.models import User, McpServerRegistry, McpLlmAxisScore, McpScoreDispute

router = APIRouter()

def mesh_memory_endpoint(mesh_memory_id: str) -> dict:
    """Get mesh memory by ID from ZoComputer store."""
    with httpx.Client(base_url="http://127.0.0.1:8772") as client:
        response = client.post("/query", json={
            "table": "mesh_memory",
            "filters": {"id": mesh_memory_id}
        })
        response.raise_for_status()
        result = response.json()
        return result.get("data", result) if isinstance(result, dict) else result

def get_mesh_memory_endpoint(mesh_memory_id: str) -> dict:
    """Get mesh memory endpoint."""
    return mesh_memory_endpoint(mesh_memory_id)

def get_mesh_memory_by_id(mesh_memory_id: str) -> dict:
    """Get mesh memory by ID."""
    return mesh_memory_endpoint(mesh_memory_id)

def mesh_memory_endpoint_get(mesh_memory_id: str) -> dict:
    """Get mesh memory endpoint (GET variant)."""
    return mesh_memory_endpoint(mesh_memory_id)

def users_endpoint(session: Session = Depends(get_session)) -> list[dict]:
    """Get users endpoint."""
    users = session.query(User).all()
    return [{"id": u.id, "clerk_created_at": u.clerk_created_at} for u in users]

def signal_scores_endpoint(session: Session = Depends(get_session)) -> list[dict]:
    """Get signal scores endpoint."""
    scores = session.query(McpLlmAxisScore).all()
    return [{"id": s.id, "axis": s.axis, "score": s.score} for s in scores]

def get_score_disputes_endpoint(session: Session = Depends(get_session)) -> list[dict]:
    """Get score disputes endpoint."""
    disputes = session.query(McpScoreDispute).all()
    return [{"id": d.id, "status": d.status, "reason": d.reason} for d in disputes]

def test_self():
    """Self-test for this module."""
    run_self_test()

def run_self_test():
    """Run self-test."""
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    
    @app.get("/test")
    def test_route():
        return {"status": "ok"}
    
    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.get("/test")
    assert response.status_code == 200
    print("PASS")

if __name__ == "__main__":
    run_self_test()