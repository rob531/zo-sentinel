# deps: fastapi, pydantic, sqlalchemy, sqlmodel, passlib
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, Org, User, ApiKey
from typing import List, Optional

router = APIRouter()

class VerdictBreakdown(BaseModel):
    server_id: str
    axis_name: str
    label: str
    label_index: int
    probs: dict
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    escalated_to: Optional[str]
    decision_rule_version: str
    model_version: str
    adapter_sha256: str
    scored_at: str

class ServerVerdict(BaseModel):
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

@router.get("/verdicts/{server_id}", response_model=List[VerdictBreakdown])
async def get_verdict_breakdown(server_id: str, org_id: str, db: Session = Depends(get_session)):
    # Query the McpLlmAxisScore table for the given server_id and org_id
    axis_scores = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.org_id == org_id
    ).all()
    
    if not axis_scores:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No verdict breakdown found for the given server_id"
        )
    
    return axis_scores

@router.get("/servers/{server_id}", response_model=ServerVerdict)
async def get_server_verdict(server_id: str, org_id: str, db: Session = Depends(get_session)):
    # Query the McpServerRegistry table for the given server_id and org_id
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id,
        McpServerRegistry.org_id == org_id
    ).first()
    
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No server found for the given server_id"
        )
    
    return server

if __name__ == "__main__":
    import sqlite3
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the get_session dependency to use a SQLite session for testing
    SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # Create the test database and tables
    Base.metadata.create_all(bind=engine)

    # Create a test client
    client = TestClient(app)

    # Test data
    test_server_id = "test_server"
    test_org_id = "test_org"
    test_axis_scores = [
        {
            "server_id": test_server_id,
            "axis_name": "overall_risk",
            "label": "HIGH",
            "label_index": 2,
            "probs": {"HIGH": 0.8, "MEDIUM": 0.15, "LOW": 0.05},
            "p_top": 0.8,
            "p_critical": 0.2,
            "p_danger": 0.3,
            "escalated": False,
            "escalated_to": None,
            "decision_rule_version": "1.0",
            "model_version": "1.0",
            "adapter_sha256": "abc123",
            "scored_at": "2023-01-01T00:00:00"
        }
    ]
    test_server = {
        "server_id": test_server_id,
        "name": "Test Server",
        "registry_source": "Test Source",
        "url": "http://test.com",
        "description": "Test Description",
        "trust_score": 0.9,
        "verdict": "HIGH",
        "verdict_reasoning": "Test Reasoning",
        "confidence": 0.8,
        "risk_tier": "HIGH",
        "scan_count": 10,
        "first_seen": "2023-01-01T00:00:00",
        "last_seen": "2023-01-01T00:00:00",
        "last_scanned": "2023-01-01T00:00:00",
        "last_assessed": "2023-01-01T00:00:00",
        "meta": {}
    }

    # Insert test data
    db = TestingSessionLocal()
    for score in test_axis_scores:
        db.add(McpLlmAxisScore(**score))
    db.add(McpServerRegistry(**test_server))
    db.commit()
    db.close()

    # Test the endpoints
    response = client.get(f"/verdicts/{test_server_id}?org_id={test_org_id}")
    assert response.status_code == 200
    assert len(response.json()) == 1

    response = client.get(f"/servers/{test_server_id}?org_id={test_org_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Test Server"

    print("PASS")