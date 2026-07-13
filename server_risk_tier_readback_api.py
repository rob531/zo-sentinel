from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, TrustGatingOverride
from datetime import datetime
from typing import Optional

router = APIRouter()

class RiskTierResponse(BaseModel):
    server_id: str
    risk_tier: str
    trust_gate_override: Optional[str] = None
    last_scored: datetime
    verdict: str

def get_risk_tier_from_registry(server_id: str, session: Session) -> str:
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server.risk_tier

def check_critical_axis_override(server_id: str, session: Session) -> bool:
    critical_axis = session.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id,
        MCPLLMAxisScores.axis == "CRITICAL"
    ).first()
    return critical_axis is not None

def get_trust_gate_override(server_id: str, session: Session) -> Optional[str]:
    override = session.query(TrustGatingOverride).filter(
        TrustGatingOverride.server_id == server_id
    ).first()
    return override.override_reason if override else None

def get_last_scored(server_id: str, session: Session) -> datetime:
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server.last_assessed

@router.get("/servers/{server_id}/risk-tier", response_model=RiskTierResponse)
async def get_server_risk_tier(server_id: str, session: Session = Depends(get_session)) -> RiskTierResponse:
    risk_tier = get_risk_tier_from_registry(server_id, session)
    if check_critical_axis_override(server_id, session):
        risk_tier = "HIGH_RISK_ISOLATED"

    trust_gate_override = get_trust_gate_override(server_id, session)
    last_scored = get_last_scored(server_id, session)

    verdict = "HIGH_RISK_ISOLATED" if risk_tier == "HIGH_RISK_ISOLATED" else risk_tier

    return RiskTierResponse(
        server_id=server_id,
        risk_tier=risk_tier,
        trust_gate_override=trust_gate_override,
        last_scored=last_scored,
        verdict=verdict
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import SessionLocal
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Mock data
    def get_mock_session():
        session = SessionLocal()
        # Add mock data
        session.add(MCPServerRegistry(
            server_id="test-server-1",
            risk_tier="MEDIUM_RISK",
            last_assessed=datetime.now()
        ))
        session.add(MCPLLMAxisScores(
            server_id="test-server-1",
            axis="CRITICAL",
            score=1.0
        ))
        session.add(TrustGatingOverride(
            server_id="test-server-1",
            override_reason="Test override"
        ))
        session.commit()
        return session

    # Override dependency for testing
    app.dependency_overrides[get_session] = get_mock_session

    # Create test client
    client = TestClient(router)

    # Test endpoint
    response = client.get("/servers/test-server-1/risk-tier")
    assert response.status_code == 200
    assert response.json()["server_id"] == "test-server-1"
    assert response.json()["risk_tier"] == "HIGH_RISK_ISOLATED"
    assert response.json()["trust_gate_override"] == "Test override"
    assert "last_scored" in response.json()
    assert response.json()["verdict"] == "HIGH_RISK_ISOLATED"

    print("PASS")