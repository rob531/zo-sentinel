from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Dict, Optional
from app.db import get_session
from app.models import MCPServerRegistry
from datetime import datetime

router = APIRouter()

class RiskTierOverview(BaseModel):
    tier: str
    count: int
    percentage: float

class RiskTierOverviewResponse(BaseModel):
    tiers: List[RiskTierOverview]
    total_servers: int
    server_timestamp: Optional[datetime]

@router.get("/risk_tier/overview", response_model=RiskTierOverviewResponse)
def get_risk_tier_overview(db: Session = Depends(get_session)):
    # Query the database for all servers and their risk tiers
    servers = db.query(MCPServerRegistry).all()

    # Initialize a dictionary to hold the counts for each risk tier
    tier_counts = {
        "TRUSTED_GENERAL": 0,
        "TRUSTED_RESEARCH": 0,
        "ENTERPRISE_CONTROLLED": 0,
        "CAUTION_LIMITED": 0,
        "HIGH_RISK_ISOLATED": 0,
        "HIGH_RISK_KNOWN_THREAT": 0,
        "INSUFFICIENT": 0
    }

    # Count the servers in each risk tier
    for server in servers:
        if server.risk_tier:
            tier_counts[server.risk_tier] += 1
        else:
            tier_counts["INSUFFICIENT"] += 1

    # Calculate the total number of servers
    total_servers = sum(tier_counts.values())

    # Calculate the percentage for each risk tier
    tier_overviews = []
    for tier, count in tier_counts.items():
        percentage = (count / total_servers * 100) if total_servers > 0 else 0.0
        tier_overviews.append(RiskTierOverview(tier=tier, count=count, percentage=percentage))

    # Get the most recent server timestamp
    server_timestamp = max(server.last_seen for server in servers) if servers else None

    return RiskTierOverviewResponse(
        tiers=tier_overviews,
        total_servers=total_servers,
        server_timestamp=server_timestamp
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry
    from app.main import app
    from app.dependency_overrides import get_session

    # Create a test database session
    Base.metadata.create_all(bind=engine)
    test_session = get_session()

    # Add test data
    test_servers = [
        MCPServerRegistry(
            server_id="server1",
            name="Test Server 1",
            risk_tier="TRUSTED_GENERAL",
            last_seen=datetime.now()
        ),
        MCPServerRegistry(
            server_id="server2",
            name="Test Server 2",
            risk_tier="TRUSTED_GENERAL",
            last_seen=datetime.now()
        ),
        MCPServerRegistry(
            server_id="server3",
            name="Test Server 3",
            risk_tier="CAUTION_LIMITED",
            last_seen=datetime.now()
        )
    ]
    test_session.add_all(test_servers)
    test_session.commit()

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/risk_tier/overview")
    assert response.status_code == 200
    data = response.json()

    # Verify the response
    assert data["total_servers"] == 3
    tiers = {tier["tier"]: tier for tier in data["tiers"]}
    assert tiers["TRUSTED_GENERAL"]["count"] == 2
    assert tiers["CAUTION_LIMITED"]["count"] == 1
    assert tiers["TRUSTED_GENERAL"]["percentage"] + tiers["CAUTION_LIMITED"]["percentage"] == 100.0

    print("PASS")