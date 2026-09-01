from typing import List
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

app = FastAPI()

class TierSummary(BaseModel):
    tier: str
    count: int
    percentage: float

class RiskTierOverview(BaseModel):
    total_servers: int
    tiers: List[TierSummary]

def get_risk_tier_distribution(db: Session = Depends(get_session)):
    # Query servers with their risk tiers
    servers = db.query(
        McpServerRegistry.server_id,
        McpServerRegistry.risk_tier
    ).all()

    # Count servers per tier
    tier_counts = {}
    for server in servers:
        tier = server.risk_tier
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    # Calculate total servers
    total_servers = len(servers)

    # Prepare response data
    tiers = []
    for tier, count in tier_counts.items():
        percentage = (count / total_servers) * 100
        tiers.append({
            "tier": tier,
            "count": count,
            "percentage": round(percentage, 2)
        })

    return RiskTierOverview(
        total_servers=total_servers,
        tiers=tiers
    )

@app.get("/api/risk/overview", response_model=RiskTierOverview)
async def get_overview():
    return get_risk_tier_distribution()

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Seed test data
    test_session = TestSession()
    test_session.add_all([
        McpServerRegistry(server_id=1, name="Server 1", risk_tier="TRUSTED_GENERAL"),
        McpServerRegistry(server_id=2, name="Server 2", risk_tier="TRUSTED_GENERAL"),
        McpServerRegistry(server_id=3, name="Server 3", risk_tier="TRUSTED_RESEARCH"),
        McpServerRegistry(server_id=4, name="Server 4", risk_tier="TRUSTED_RESEARCH"),
        McpServerRegistry(server_id=5, name="Server 5", risk_tier="ENTERPRISE_CONTROLLED"),
        McpServerRegistry(server_id=6, name="Server 6", risk_tier="ENTERPRISE_CONTROLLED"),
        McpServerRegistry(server_id=7, name="Server 7", risk_tier="CAUTION_LIMITED"),
        McpServerRegistry(server_id=8, name="Server 8", risk_tier="CAUTION_LIMITED"),
        McpServerRegistry(server_id=9, name="Server 9", risk_tier="HIGH_RISK_ISOLATED"),
        McpServerRegistry(server_id=10, name="Server 10", risk_tier="HIGH_RISK_ISOLATED"),
        McpServerRegistry(server_id=11, name="Server 11", risk_tier="KNOWN_THREAT"),
        McpServerRegistry(server_id=12, name="Server 12", risk_tier="KNOWN_THREAT"),
        McpServerRegistry(server_id=13, name="Server 13", risk_tier="INSUFFICIENT"),
        McpServerRegistry(server_id=14, name="Server 14", risk_tier="INSUFFICIENT"),
    ])
    test_session.commit()

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/overview")
    assert response.status_code == 200
    data = response.json()

    # Verify the response
    assert data["total_servers"] == 14
    tier_counts = {
        "TRUSTED_GENERAL": 2,
        "TRUSTED_RESEARCH": 2,
        "ENTERPRISE_CONTROLLED": 2,
        "CAUTION_LIMITED": 2,
        "HIGH_RISK_ISOLATED": 2,
        "KNOWN_THREAT": 2,
        "INSUFFICIENT": 2
    }
    for tier_data in data["tiers"]:
        assert tier_data["count"] == tier_counts[tier_data["tier"]]

    print("PASS")