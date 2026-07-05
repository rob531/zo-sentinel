from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List
from sqlalchemy import select
from app.db import get_session
from app.models import MCPRiskTierDefinitionHistory

router = APIRouter()

class RiskTierDefinitionHistory(BaseModel):
    risk_tier: int
    definition: str
    criteria_version: int
    effective_from: str
    effective_to: str
    is_current: bool

@router.get("/risk-tier-definition-history-dashboard", response_model=List[RiskTierDefinitionHistory])
async def get_risk_tier_definition_history(session=Depends(get_session)):
    stmt = select(
        MCPRiskTierDefinitionHistory.risk_tier,
        MCPRiskTierDefinitionHistory.definition,
        MCPRiskTierDefinitionHistory.criteria_version,
        MCPRiskTierDefinitionHistory.effective_from,
        MCPRiskTierDefinitionHistory.effective_to,
        MCPRiskTierDefinitionHistory.is_current
    )
    result = session.execute(stmt)
    return [RiskTierDefinitionHistory(**row._asdict()) for row in result]

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPRiskTierDefinitionHistory
    from app.db import get_session
    from sqlalchemy.orm import Session

    # Override the session for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)

    # Seed test data
    with Session(test_engine) as session:
        test_data = [
            MCPRiskTierDefinitionHistory(
                risk_tier=1,
                definition="Low risk",
                criteria_version=1,
                effective_from="2023-01-01",
                effective_to="2023-12-31",
                is_current=True
            ),
            MCPRiskTierDefinitionHistory(
                risk_tier=2,
                definition="Medium risk",
                criteria_version=1,
                effective_from="2023-01-01",
                effective_to="2023-12-31",
                is_current=True
            ),
            MCPRiskTierDefinitionHistory(
                risk_tier=3,
                definition="High risk",
                criteria_version=1,
                effective_from="2023-01-01",
                effective_to="2023-12-31",
                is_current=True
            ),
            MCPRiskTierDefinitionHistory(
                risk_tier=4,
                definition="Very high risk",
                criteria_version=1,
                effective_from="2023-01-01",
                effective_to="2023-12-31",
                is_current=True
            ),
            MCPRiskTierDefinitionHistory(
                risk_tier=5,
                definition="Extreme risk",
                criteria_version=1,
                effective_from="2023-01-01",
                effective_to="2023-12-31",
                is_current=True
            ),
            MCPRiskTierDefinitionHistory(
                risk_tier=6,
                definition="Critical risk",
                criteria_version=1,
                effective_from="2023-01-01",
                effective_to="2023-12-31",
                is_current=True
            ),
            MCPRiskTierDefinitionHistory(
                risk_tier=7,
                definition="Catastrophic risk",
                criteria_version=1,
                effective_from="2023-01-01",
                effective_to="2023-12-31",
                is_current=True
            )
        ]
        session.add_all(test_data)
        session.commit()

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: Session(test_engine)

    # Create a test client
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/risk-tier-definition-history-dashboard")
    assert response.status_code == 200
    assert len(response.json()) == 7
    print("PASS")