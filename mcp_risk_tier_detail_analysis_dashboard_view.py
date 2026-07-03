from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPRiskRegister
from typing import List, Dict, Any

router = APIRouter()

class RiskTierDetail(BaseModel):
    tier: str
    details: Dict[str, Any]

class RiskTierDetailAnalysisResponse(BaseModel):
    analysis: List[RiskTierDetail]

@router.get("/risk-tier-detail-analysis", response_model=RiskTierDetailAnalysisResponse)
def get_risk_tier_detail_analysis(db: Session = Depends(get_session)) -> RiskTierDetailAnalysisResponse:
    risk_registers = db.query(MCPRiskRegister).all()

    analysis = []
    for register in risk_registers:
        tier = register.risk_tier
        details = {
            "id": register.id,
            "risk_description": register.risk_description,
            "impact_score": register.impact_score,
            "likelihood_score": register.likelihood_score,
            "mitigation_strategy": register.mitigation_strategy,
            "owner": register.owner,
            "status": register.status,
            "last_updated": register.last_updated
        }
        analysis.append(RiskTierDetail(tier=tier, details=details))

    return RiskTierDetailAnalysisResponse(analysis=analysis)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPRiskRegister
    from sqlalchemy.orm import Session

    # Override the session for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)

    def override_get_session():
        session = Session(test_engine)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    with Session(test_engine) as session:
        session.add_all([
            MCPRiskRegister(
                risk_description="High impact risk",
                impact_score=9,
                likelihood_score=8,
                risk_tier="High",
                mitigation_strategy="Immediate action required",
                owner="admin",
                status="Active",
                last_updated="2023-01-01"
            ),
            MCPRiskRegister(
                risk_description="Medium impact risk",
                impact_score=6,
                likelihood_score=5,
                risk_tier="Medium",
                mitigation_strategy="Monitor closely",
                owner="user1",
                status="Active",
                last_updated="2023-01-02"
            )
        ])
        session.commit()

    # Create test client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/risk-tier-detail-analysis")
    assert response.status_code == 200
    assert len(response.json()["analysis"]) == 2
    print("PASS")