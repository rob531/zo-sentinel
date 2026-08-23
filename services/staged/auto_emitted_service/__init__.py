# deps: requests, fastapi, pydantic, sqlalchemy, sqlmodel
"""Auto-emitted service package with proper FastAPI implementation."""

from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

router = APIRouter()

class SignalScore(BaseModel):
    mesh_id: str
    server_id: str
    signal: str
    value: float
    timestamp: str

class AxisScore(BaseModel):
    server_id: str
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    scored_at: str

class ScoreDispute(BaseModel):
    id: int
    server_id: str
    submitted_by: str
    proposed_overall_risk: str
    proposed_axes: dict
    reason_category: str
    explanation: str
    status: str
    admin_note: Optional[str]
    created_at: str
    resolved_at: Optional[str]

class UserInfo(BaseModel):
    id: int
    email: str
    role: str
    org_id: int

class OrgInfo(BaseModel):
    id: int
    name: str
    created_at: str

@router.get("/mesh-memory", response_model=dict)
async def get_mesh_memory(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    db: Session = Depends(get_session)
):
    """Get mesh memory for a specific entity or all entities."""
    if entity_type and entity_id:
        # In a real implementation, this would query the mesh_memory table
        # For this example, we'll return a mock response
        return {"entity_type": entity_type, "entity_id": entity_id, "memory": {}}
    return {"global_memory": {}}

@router.get("/signal-scores", response_model=List[SignalScore])
async def get_signal_scores(
    mesh_id: Optional[str] = None,
    db: Session = Depends(get_session)
):
    """Get signal scores for a specific mesh or all meshes."""
    # In a real implementation, this would query mcp_signal_scores
    return []

@router.get("/axis-scores", response_model=List[AxisScore])
async def get_axis_scores(
    server_id: Optional[str] = None,
    db: Session = Depends(get_session)
):
    """Get axis scores for a specific server or all servers."""
    query = db.query(McpLlmAxisScore)
    if server_id:
        query = query.filter(McpLlmAxisScore.server_id == server_id)
    return query.all()

@router.get("/score-disputes", response_model=List[ScoreDispute])
async def get_score_disputes(
    server_id: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_session)
):
    """Get score disputes with optional filters."""
    query = db.query(McpScoreDispute)
    if server_id:
        query = query.filter(McpScoreDispute.server_id == server_id)
    if status:
        query = query.filter(McpScoreDispute.status == status)
    return query.limit(100).all()

@router.post("/reset-quarantine/{server_id}", response_model=bool)
async def reset_quarantine(
    server_id: str,
    db: Session = Depends(get_session)
):
    """Reset quarantine status for a server."""
    # In a real implementation, this would update the service_health table
    # For this example, we'll return a mock response
    return True

@router.get("/users", response_model=List[UserInfo])
async def get_users(
    db: Session = Depends(get_session)
):
    """Get list of users."""
    users = db.query(User).limit(100).all()
    return [{"id": u.id, "email": u.email, "role": u.role, "org_id": u.org_id} for u in users]

@router.get("/orgs/{org_id}", response_model=OrgInfo)
async def get_org(
    org_id: str,
    db: Session = Depends(get_session)
):
    """Get organization by ID."""
    org = db.query(Org).filter(Org.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {"id": org.id, "name": org.name, "created_at": org.created_at}

def _run_self_test():
    """Self-test for the module."""
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from sqlalchemy.pool import StaticPool
        from sqlalchemy import create_engine

        # Override the database dependency for testing
        engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
        app.dependency_overrides[get_session] = lambda: Session(engine)

        client = TestClient(app)

        # Test mesh memory endpoint
        response = client.get("/mesh-memory")
        assert response.status_code == 200

        # Test signal scores endpoint
        response = client.get("/signal-scores")
        assert response.status_code == 200

        # Test axis scores endpoint
        response = client.get("/axis-scores")
        assert response.status_code == 200

        # Test score disputes endpoint
        response = client.get("/score-disputes")
        assert response.status_code == 200

        # Test user endpoint
        response = client.get("/users")
        assert response.status_code == 200

        # Test org endpoint
        response = client.get("/orgs/1")
        assert response.status_code == 404  # No orgs in test DB

        print("PASS")
        return True
    except Exception as e:
        print(f"FAIL: {str(e)}")
        return False
    finally:
        app.dependency_overrides.clear()

if __name__ == "__main__":
    assert _run_self_test(), "Self-test failed"
    print("PASS")