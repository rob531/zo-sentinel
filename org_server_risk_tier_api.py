from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict
from app.db import get_session
from app.models import MCPServerRegistry
import httpx
import json
from sqlalchemy.orm import Session

router = APIRouter()

class RiskTierDistribution(BaseModel):
    risk_tiers: Dict[str, int]

@router.get("/orgs/{org_id}/servers/risk_tiers", response_model=RiskTierDistribution)
async def get_risk_tier_distribution(org_id: str, db: Session = Depends(get_session)):
    try:
        servers = db.query(MCPServerRegistry).filter(MCPServerRegistry.org_id == org_id).all()
        risk_tier_counts = {}

        for server in servers:
            risk_tier = server.risk_tier
            if risk_tier in risk_tier_counts:
                risk_tier_counts[risk_tier] += 1
            else:
                risk_tier_counts[risk_tier] = 1

        return {"risk_tiers": risk_tier_counts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == '__main__':
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Populate test data
    test_session = TestSession()
    test_session.add_all([
        MCPServerRegistry(org_id="test_org", risk_tier="TRUSTED_GENERAL"),
        MCPServerRegistry(org_id="test_org", risk_tier="HIGH_RISK_ISOLATED"),
        MCPServerRegistry(org_id="test_org", risk_tier="TRUSTED_GENERAL"),
        MCPServerRegistry(org_id="other_org", risk_tier="CAUTION_LIMITED"),
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/orgs/test_org/servers/risk_tiers")
    assert response.status_code == 200
    assert response.json() == {
        "risk_tiers": {
            "TRUSTED_GENERAL": 2,
            "HIGH_RISK_ISOLATED": 1
        }
    }

    print("PASS")