# deps: fastapi, pydantic, sqlalchemy, passlib
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, Org, User, ApiKey

router = APIRouter()

class AxisScore(BaseModel):
    server_id: str
    axis_name: str
    label: str
    label_index: int
    probs: dict
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    escalated_to: str
    decision_rule_version: str
    model_version: str
    adapter_sha256: str
    scored_at: str

class ServerRegistry(BaseModel):
    server_id: str
    name: str
    registry_source: str
    url: str
    description: str
    trust_score: float
    verdict: str
    verdict_reasoning: str
    confidence: float
    risk_tier: str
    scan_count: int
    first_seen: str
    last_seen: str
    last_scanned: str
    last_assessed: str
    meta: dict

@router.get("/axis_scores/", response_model=List[AxisScore])
async def get_axis_scores(db: Session = Depends(get_session)):
    axis_scores = db.query(McpLlmAxisScore).all()
    return axis_scores

@router.get("/server_registry/", response_model=List[ServerRegistry])
async def get_server_registry(db: Session = Depends(get_session)):
    server_registry = db.query(McpServerRegistry).all()
    return server_registry

@router.get("/axis_scores/{server_id}", response_model=List[AxisScore])
async def get_axis_scores_by_server(server_id: str, db: Session = Depends(get_session)):
    axis_scores = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()
    if not axis_scores:
        raise HTTPException(status_code=404, detail="Server not found")
    return axis_scores

@router.get("/server_registry/{server_id}", response_model=ServerRegistry)
async def get_server_registry_by_id(server_id: str, db: Session = Depends(get_session)):
    server_registry = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server_registry:
        raise HTTPException(status_code=404, detail="Server not found")
    return server_registry

if __name__ == "__main__":
    import os
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Create a test database
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Override the get_session dependency to use the test database
    def override_get_session():
        try:
            db = Session(engine)
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create the tables
    Base.metadata.create_all(bind=engine)

    # Seed the test data
    db = Session(engine)
    test_axis_score = McpLlmAxisScore(
        server_id="test_server",
        axis_name="overall_risk",
        label="HIGH",
        label_index=2,
        probs={"LOW": 0.1, "MEDIUM": 0.3, "HIGH": 0.6},
        p_top=0.6,
        p_critical=0.0,
        p_danger=0.0,
        escalated=False,
        escalated_to="",
        decision_rule_version="1.0",
        model_version="1.0",
        adapter_sha256="test_sha",
        scored_at="2023-01-01T00:00:00"
    )
    db.add(test_axis_score)
    db.commit()
    db.close()

    # Run the self-test
    client = TestClient(app)
    response = client.get("/axis_scores/test_server")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["server_id"] == "test_server"
    print("PASS")