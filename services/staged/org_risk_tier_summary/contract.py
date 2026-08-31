from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")

class RiskTierSummary(BaseModel):
    risk_tier: str
    server_count: int
    avg_p_top: float

class OrgRiskTierSummaryResponse(BaseModel):
    org_id: str
    summary: List[RiskTierSummary]

@router.get("/orgs/{org_id}/risk_tier_summary", response_model=OrgRiskTierSummaryResponse)
def get_org_risk_tier_summary(org_id: str, session: Session = Depends(get_session)):
    query = """
    SELECT
        r.risk_tier,
        COUNT(DISTINCT r.server_id) as server_count,
        AVG(s.p_top) as avg_p_top
    FROM
        McpServerRegistry r
    JOIN
        McpLlmAxisScore s ON r.server_id = s.server_id
    WHERE
        r.org_id = :org_id
    GROUP BY
        r.risk_tier
    """
    result = session.execute(query, {"org_id": org_id}).fetchall()

    if not result:
        raise HTTPException(status_code=404, detail="Organization not found or no servers with risk tiers")

    summary = [
        RiskTierSummary(
            risk_tier=row.risk_tier,
            server_count=row.server_count,
            avg_p_top=float(row.avg_p_top) if row.avg_p_top is not None else 0.0
        )
        for row in result
    ]

    return OrgRiskTierSummaryResponse(org_id=org_id, summary=summary)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Create in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Insert test data
    with SessionLocal() as session:
        # Insert test organizations
        session.execute("INSERT INTO orgs (id, name) VALUES ('org1', 'Test Org 1'), ('org2', 'Test Org 2')")

        # Insert test servers with varying risk tiers
        session.execute("""
        INSERT INTO McpServerRegistry (server_id, org_id, risk_tier)
        VALUES
            ('server1', 'org1', 'low'),
            ('server2', 'org1', 'medium'),
            ('server3', 'org2', 'high')
        """)

        # Insert test axis scores with varying p_top values
        session.execute("""
        INSERT INTO McpLlmAxisScore (server_id, axis_name, p_top)
        VALUES
            ('server1', 'axis1', 0.8),
            ('server1', 'axis2', 0.7),
            ('server2', 'axis1', 0.6),
            ('server2', 'axis2', 0.5),
            ('server3', 'axis1', 0.4),
            ('server3', 'axis2', 0.3)
        """)
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/orgs/org1/risk_tier_summary")
    assert response.status_code == 200

    data = response.json()
    assert data["org_id"] == "org1"
    assert len(data["summary"]) == 2

    # Verify the summary contains expected tiers with correct counts and averages
    tiers = {item["risk_tier"]: item for item in data["summary"]}
    assert tiers["low"]["server_count"] == 1
    assert tiers["low"]["avg_p_top"] == 0.75  # (0.8 + 0.7) / 2
    assert tiers["medium"]["server_count"] == 1
    assert tiers["medium"]["avg_p_top"] == 0.55  # (0.6 + 0.5) / 2

    print("PASS")