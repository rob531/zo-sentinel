from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from app.db import get_session
from app.models import McpServerRegistry
import requests
from pydantic import BaseModel

router = APIRouter(prefix="/api/risk")

class TierCount(BaseModel):
    risk_tier: str
    count: int

class RiskTierResponse(BaseModel):
    org_id: str
    tiers: List[TierCount]
    total_servers: int

def query_write_service(sql: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """Helper to query write_service with parameterized SQL."""
    if params is None:
        params = {}
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql, "params": params}
    )
    response.raise_for_status()
    return response.json()

@router.get("/tiers_by_org", response_model=RiskTierResponse)
async def get_risk_tiers_by_org(org_id: str, session: Session = Depends(get_session)):
    """Get risk tier distribution for all MCP servers belonging to a given organization."""
    # Query server registry for servers in the org
    servers = session.query(McpServerRegistry).filter(McpServerRegistry.org_id == org_id).all()

    if not servers:
        raise HTTPException(status_code=404, detail="No servers found for the given org_id")

    # Count servers per risk tier
    tier_counts = {}
    for server in servers:
        tier = server.risk_tier
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    # Prepare response
    tiers = [{"risk_tier": tier, "count": count} for tier, count in tier_counts.items()]
    response = {
        "org_id": org_id,
        "tiers": tiers,
        "total_servers": len(servers)
    }

    return response

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Override get_session for testing
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Insert test data
    with TestSessionLocal() as session:
        session.execute("""
        INSERT INTO McpServerRegistry (server_id, org_id, risk_tier)
        VALUES
            ('server1', 'org123', 'HIGH_RISK_ISOLATED'),
            ('server2', 'org123', 'CAUTION_LIMITED'),
            ('server3', 'org123', 'HIGH_RISK_ISOLATED')
        """)
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/tiers_by_org?org_id=org123")

    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert len(data["tiers"]) == 2
    high_risk_count = next(tier["count"] for tier in data["tiers"] if tier["risk_tier"] == "HIGH_RISK_ISOLATED")
    assert high_risk_count == 2
    assert data["total_servers"] == 3

    print("PASS")