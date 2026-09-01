from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from app.db import get_session
from app.models import McpServerRegistry
import requests

def get_risk_tiers_by_org(org_id: str, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """
    Retrieve risk tier distribution for all MCP servers belonging to a given organization.

    Args:
        org_id: The organization ID to filter servers by.
        session: SQLAlchemy session for database access.

    Returns:
        A dictionary containing the organization ID, risk tier distribution, and total server count.
    """
    # Query the database for servers belonging to the specified organization
    servers = session.query(McpServerRegistry).filter(McpServerRegistry.org_id == org_id).all()

    # Count the number of servers in each risk tier
    tier_counts = {}
    for server in servers:
        risk_tier = server.risk_tier
        if risk_tier in tier_counts:
            tier_counts[risk_tier] += 1
        else:
            tier_counts[risk_tier] = 1

    # Convert the tier counts to the required output format
    tiers = [{"risk_tier": tier, "count": count} for tier, count in tier_counts.items()]

    return {
        "org_id": org_id,
        "tiers": tiers,
        "total_servers": len(servers)
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import Base, engine
    from app.models import McpServerRegistry

    # Override the database session for testing
    app.dependency_overrides[get_session] = lambda: Session(engine)

    # Create a test database in memory
    Base.metadata.create_all(engine)

    # Insert test data
    test_session = Session(engine)
    test_session.add_all([
        McpServerRegistry(server_id="server1", org_id="org123", risk_tier="HIGH_RISK_ISOLATED"),
        McpServerRegistry(server_id="server2", org_id="org123", risk_tier="CAUTION_LIMITED"),
        McpServerRegistry(server_id="server3", org_id="org123", risk_tier="HIGH_RISK_ISOLATED")
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/tiers_by_org?org_id=org123")

    # Assert the response
    assert response.status_code == 200
    response_data = response.json()
    assert len(response_data["tiers"]) == 2
    assert response_data["tiers"][0]["risk_tier"] == "HIGH_RISK_ISOLATED"
    assert response_data["tiers"][0]["count"] == 2
    assert response_data["total_servers"] == 3

    print("PASS")