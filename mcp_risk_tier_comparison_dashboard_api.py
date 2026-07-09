from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry
from typing import Dict, Any
from pydantic import BaseModel

router = APIRouter()

class RiskTierComparison(BaseModel):
    group: str
    tier_counts: Dict[str, int]

@router.get("/risk-tier-comparison", response_model=Dict[str, Dict[str, int]])
def get_risk_tier_comparison(db: Session = Depends(get_session)) -> Dict[str, Dict[str, int]]:
    query = db.query(
        MCPServerRegistry.group,
        MCPServerRegistry.risk_tier,
        MCPServerRegistry.id.count().label("count")
    ).group_by(
        MCPServerRegistry.group,
        MCPServerRegistry.risk_tier
    ).all()

    result = {}
    for group, tier, count in query:
        if group not in result:
            result[group] = {}
        result[group][tier] = count

    return result

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry
    from sqlalchemy.orm import sessionmaker

    # Override the dependency for testing
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    test_data = [
        MCPServerRegistry(group="group1", risk_tier="low"),
        MCPServerRegistry(group="group1", risk_tier="medium"),
        MCPServerRegistry(group="group1", risk_tier="high"),
        MCPServerRegistry(group="group2", risk_tier="low"),
        MCPServerRegistry(group="group2", risk_tier="low"),
        MCPServerRegistry(group="group2", risk_tier="medium"),
    ]
    db.add_all(test_data)
    db.commit()

    # Test the endpoint
    from app.main import app
    client = TestClient(app)
    response = client.get("/risk-tier-comparison")
    assert response.status_code == 200
    assert response.json() == {
        "group1": {"low": 1, "medium": 1, "high": 1},
        "group2": {"low": 2, "medium": 1}
    }

    print("PASS")