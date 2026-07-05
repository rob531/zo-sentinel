from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPDefinitionHistory

router = APIRouter()

class RiskTierDefinitionHistoryAnalysis(BaseModel):
    risk_tier: str
    definition: str
    criteria_version: str
    effective_from: datetime
    effective_to: Optional[datetime]
    analysis: str

@router.get("/servers/{server_id}/risk-tier-definition-history-analysis", response_model=List[RiskTierDefinitionHistoryAnalysis])
async def get_risk_tier_definition_history_analysis(server_id: int, session: Session = Depends(get_session)):
    # Query the risk tier definition history for the given server_id
    history = session.query(MCPDefinitionHistory).filter(
        MCPDefinitionHistory.server_id == server_id
    ).all()

    if not history:
        raise HTTPException(status_code=404, detail="No risk tier definition history found for the given server_id")

    # Process the history to include analysis with rule-override for CRITICAL axis
    result = []
    for record in history:
        analysis = record.analysis
        if "CRITICAL" in record.definition.upper():
            analysis += " (Rule-override: CRITICAL axis forces the tier)"

        result.append({
            "risk_tier": record.risk_tier,
            "definition": record.definition,
            "criteria_version": record.criteria_version,
            "effective_from": record.effective_from,
            "effective_to": record.effective_to,
            "analysis": analysis
        })

    return result

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPDefinitionHistory
    from sqlalchemy.orm import sessionmaker

    # Override the dependency for testing
    from app.db import get_session as original_get_session
    from app.dependency_overrides import dependency_overrides
    dependency_overrides[original_get_session] = lambda: sessionmaker(bind=engine)()

    # Create a test client
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Seed the in-memory store with test data
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    test_data = [
        MCPDefinitionHistory(
            server_id=1,
            risk_tier="High",
            definition="High risk due to critical vulnerabilities",
            criteria_version="1.0",
            effective_from=datetime(2023, 1, 1),
            effective_to=datetime(2023, 1, 31),
            analysis="High risk analysis"
        ),
        MCPDefinitionHistory(
            server_id=1,
            risk_tier="Critical",
            definition="Critical risk due to severe vulnerabilities",
            criteria_version="1.1",
            effective_from=datetime(2023, 2, 1),
            effective_to=None,
            analysis="Critical risk analysis"
        )
    ]

    session.add_all(test_data)
    session.commit()

    # Test the endpoint
    response = client.get("/servers/1/risk-tier-definition-history-analysis")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.json()[0]["risk_tier"] == "High"
    assert response.json()[1]["risk_tier"] == "Critical"
    assert "Rule-override: CRITICAL axis forces the tier" in response.json()[1]["analysis"]

    print("PASS")