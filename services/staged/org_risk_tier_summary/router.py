from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List

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

@router.get(
    "/orgs/{org_id}/risk_tier_summary",
    response_model=OrgRiskTierSummaryResponse,
    response_model_exclude_none=True,
)
async def get_org_risk_tier_summary(
    org_id: str,
    session: Session = Depends(get_session),
) -> OrgRiskTierSummaryResponse:
    # Query to get server count and average p_top for each risk tier
    query = """
        SELECT
            s.risk_tier,
            COUNT(DISTINCT s.server_id) as server_count,
            AVG(a.p_top) as avg_p_top
        FROM
            McpServerRegistry s
        LEFT JOIN
            McpLlmAxisScore a ON s.server_id = a.server_id
        WHERE
            s.org_id = :org_id
        GROUP BY
            s.risk_tier
    """
    result = session.execute(query, {"org_id": org_id}).fetchall()

    if not result:
        raise HTTPException(status_code=404, detail="Organization not found")

    summary = [
        RiskTierSummary(
            risk_tier=row.risk_tier,
            server_count=row.server_count,
            avg_p_top=round(row.avg_p_top, 4) if row.avg_p_top is not None else None,
        )
        for row in result
    ]

    return OrgRiskTierSummaryResponse(org_id=org_id, summary=summary)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create in-memory SQLite database for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)

    # Create tables
    McpServerRegistry.metadata.create_all(test_engine)
    McpLlmAxisScore.metadata.create_all(test_engine)

    # Override the dependency for testing
    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Insert test data
    with TestSession() as session:
        # Insert test organizations
        session.execute(
            "INSERT INTO orgs (id, name) VALUES ('org1', 'Test Org 1'), ('org2', 'Test Org 2')"
        )

        # Insert test servers
        session.execute(
            """
            INSERT INTO McpServerRegistry (server_id, org_id, risk_tier)
            VALUES
                ('server1', 'org1', 'low'),
                ('server2', 'org1', 'medium'),
                ('server3', 'org2', 'high')
            """
        )

        # Insert test axis scores
        session.execute(
            """
            INSERT INTO McpLlmAxisScore (server_id, axis_name, p_top)
            VALUES
                ('server1', 'axis1', 0.8),
                ('server1', 'axis2', 0.7),
                ('server2', 'axis1', 0.6),
                ('server3', 'axis1', 0.9),
                ('server3', 'axis2', 0.85)
            """
        )
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/orgs/org1/risk_tier_summary")
    assert response.status_code == 200
    data = response.json()

    # Verify the response
    assert data["org_id"] == "org1"
    assert len(data["summary"]) == 2

    # Check low risk tier
    low_tier = next(item for item in data["summary"] if item["risk_tier"] == "low")
    assert low_tier["server_count"] == 1
    assert low_tier["avg_p_top"] == 0.75  # (0.8 + 0.7) / 2

    # Check medium risk tier
    medium_tier = next(item for item in data["summary"] if item["risk_tier"] == "medium")
    assert medium_tier["server_count"] == 1
    assert medium_tier["avg_p_top"] == 0.6  # Only one score

    print("PASS")