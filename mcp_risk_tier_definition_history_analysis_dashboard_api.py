from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPDefinitionHistory

router = APIRouter()

class RiskTierDefinitionHistoryAnalysis(BaseModel):
    risk_tier: str
    definition: str
    criteria_version: int
    effective_from: datetime
    effective_to: Optional[datetime]
    analysis: str

@router.get(
    "/servers/{server_id}/risk-tier-definition-history-analysis-dashboard",
    response_model=List[RiskTierDefinitionHistoryAnalysis],
)
async def get_risk_tier_definition_history_analysis(
    server_id: int, session: Session = Depends(get_session)
):
    query = session.query(MCPDefinitionHistory).filter(
        MCPDefinitionHistory.server_id == server_id
    )
    results = query.all()

    if not results:
        raise HTTPException(status_code=404, detail="No risk tier definition history found")

    response = []
    for record in results:
        response.append(
            RiskTierDefinitionHistoryAnalysis(
                risk_tier=record.risk_tier,
                definition=record.definition,
                criteria_version=record.criteria_version,
                effective_from=record.effective_from,
                effective_to=record.effective_to,
                analysis=record.analysis,
            )
        )

    return response

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base, MCPDefinitionHistory
    from datetime import datetime, timedelta

    # Setup in-memory database for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override the dependency for testing
    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    test_session = SessionLocal()
    test_data = [
        MCPDefinitionHistory(
            server_id=1,
            risk_tier="CRITICAL",
            definition="High risk due to critical axis override",
            criteria_version=1,
            effective_from=datetime(2023, 1, 1),
            effective_to=datetime(2023, 1, 31),
            analysis="Critical axis forces the tier",
        ),
        MCPDefinitionHistory(
            server_id=1,
            risk_tier="HIGH",
            definition="High risk due to multiple factors",
            criteria_version=2,
            effective_from=datetime(2023, 2, 1),
            effective_to=None,
            analysis="Multiple factors contribute to high risk",
        ),
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Create test client
    from app.main import app
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/servers/1/risk-tier-definition-history-analysis-dashboard")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.json()[0]["risk_tier"] == "CRITICAL"
    assert response.json()[1]["risk_tier"] == "HIGH"

    print("PASS")