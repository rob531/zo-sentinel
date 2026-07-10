from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import requests
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPDecisions

router = APIRouter()

class DecisionResponse(BaseModel):
    id: int
    server_id: str
    mcp_name: str
    status: str
    verdict: str
    conditions: Optional[str]
    expiry_date: Optional[datetime]
    decided_by: str
    decided_at: datetime
    risk_tier_at_decision: int

class DecisionPayload(BaseModel):
    status: str
    verdict_override: Optional[str]
    conditions: Optional[str]
    expiry_days: Optional[int]
    decided_by: str

def get_risk_tier_at_decision(server_id: str) -> int:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "query": f"SELECT risk_tier FROM mcp_llm_axis_scores WHERE server_id = '{server_id}' ORDER BY created_at DESC LIMIT 1"
        }
    )
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch risk tier")
    data = response.json()
    if not data:
        raise HTTPException(status_code=404, detail="No risk tier found for server")
    return data[0]["risk_tier"]

@router.get("/servers/{server_id}/decision", response_model=Optional[DecisionResponse])
async def get_decision(server_id: str, session=Depends(get_session)):
    decision = session.query(MCPDecisions).filter(MCPDecisions.server_id == server_id).first()
    if not decision:
        return None

    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    risk_tier = get_risk_tier_at_decision(server_id)

    return DecisionResponse(
        id=decision.id,
        server_id=decision.server_id,
        mcp_name=server.mcp_name,
        status=decision.status,
        verdict=decision.verdict,
        conditions=decision.conditions,
        expiry_date=decision.expiry_date,
        decided_by=decision.decided_by,
        decided_at=decision.decided_at,
        risk_tier_at_decision=risk_tier
    )

@router.post("/servers/{server_id}/decision", response_model=DecisionResponse)
async def create_decision(server_id: str, payload: DecisionPayload, session=Depends(get_session)):
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    verdict = payload.verdict_override if payload.verdict_override else payload.status
    expiry_date = datetime.now() + timedelta(days=payload.expiry_days) if payload.expiry_days else None

    decision = MCPDecisions(
        server_id=server_id,
        status=payload.status,
        verdict=verdict,
        conditions=payload.conditions,
        expiry_date=expiry_date,
        decided_by=payload.decided_by,
        decided_at=datetime.now()
    )

    session.add(decision)
    session.commit()

    risk_tier = get_risk_tier_at_decision(server_id)

    return DecisionResponse(
        id=decision.id,
        server_id=decision.server_id,
        mcp_name=server.mcp_name,
        status=decision.status,
        verdict=decision.verdict,
        conditions=decision.conditions,
        expiry_date=decision.expiry_date,
        decided_by=decision.decided_by,
        decided_at=decision.decided_at,
        risk_tier_at_decision=risk_tier
    )

@router.get("/servers/{server_id}/decisions", response_model=List[DecisionResponse])
async def get_decisions(server_id: str, session=Depends(get_session)):
    decisions = session.query(MCPDecisions).filter(MCPDecisions.server_id == server_id).all()
    if not decisions:
        return []

    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    result = []
    for decision in decisions:
        risk_tier = get_risk_tier_at_decision(server_id)
        result.append(
            DecisionResponse(
                id=decision.id,
                server_id=decision.server_id,
                mcp_name=server.mcp_name,
                status=decision.status,
                verdict=decision.verdict,
                conditions=decision.conditions,
                expiry_date=decision.expiry_date,
                decided_by=decision.decided_by,
                decided_at=decision.decided_at,
                risk_tier_at_decision=risk_tier
            )
        )

    return result

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Mock database for testing
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=True)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Add test data
    with TestingSessionLocal() as session:
        test_server = MCPServerRegistry(server_id="test-server-001", mcp_name="Test Server")
        session.add(test_server)
        session.commit()

    client = TestClient(app)

    # Test POST /servers/test-server-001/decision
    response = client.post(
        "/servers/test-server-001/decision",
        json={
            "status": "APPROVED",
            "verdict_override": "APPROVED",
            "conditions": None,
            "expiry_days": 30,
            "decided_by": "test-analyst"
        }
    )
    assert response.status_code == 200
    assert response.json()["server_id"] == "test-server-001"
    assert response.json()["status"] == "APPROVED"
    assert response.json()["verdict"] == "APPROVED"
    assert response.json()["conditions"] is None
    assert response.json()["expiry_date"] is not None
    assert response.json()["decided_by"] == "test-analyst"
    assert response.json()["decided_at"] is not None
    assert response.json()["risk_tier_at_decision"] is not None

    # Test GET /servers/test-server-001/decision
    response = client.get("/servers/test-server-001/decision")
    assert response.status_code == 200
    assert response.json()["server_id"] == "test-server-001"
    assert response.json()["status"] == "APPROVED"
    assert response.json()["verdict"] == "APPROVED"
    assert response.json()["conditions"] is None
    assert response.json()["expiry_date"] is not None
    assert response.json()["decided_by"] == "test-analyst"
    assert response.json()["decided_at"] is not None
    assert response.json()["risk_tier_at_decision"] is not None

    print("PASS")