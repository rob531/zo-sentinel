from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores

router = APIRouter()

class ServerRiskSummary(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    overall_risk_p_top: Optional[float]
    last_scored_ago_s: Optional[int]

class OrgFleetRiskSummary(BaseModel):
    org_id: str
    total_servers: int
    tier_distribution: dict
    avg_overall_risk_p_top: Optional[float]
    avg_exploit_surface_p_top: Optional[float]
    recent_changes_7d: int
    stale_servers_30d: int
    servers: Optional[List[ServerRiskSummary]]

@router.get("/orgs/{org_id}/fleet/risk_summary", response_model=OrgFleetRiskSummary)
async def get_org_fleet_risk_summary(
    org_id: str,
    include_servers: bool = Query(False),
    session: Session = Depends(get_session)
):
    # Get all servers for the org
    servers = session.query(MCPServerRegistry).filter(
        MCPServerRegistry.org_id == org_id
    ).all()

    if not servers:
        return OrgFleetRiskSummary(
            org_id=org_id,
            total_servers=0,
            tier_distribution={},
            avg_overall_risk_p_top=None,
            avg_exploit_surface_p_top=None,
            recent_changes_7d=0,
            stale_servers_30d=0,
            servers=None
        )

    # Get latest scores for each server
    latest_scores = session.query(
        MCPLLMAxisScores.server_id,
        MCPLLMAxisScores.overall_risk_p_top,
        MCPLLMAxisScores.exploit_surface_p_top,
        MCPLLMAxisScores.risk_tier,
        MCPLLMAxisScores.created_at
    ).filter(
        MCPLLMAxisScores.server_id.in_([s.server_id for s in servers])
    ).order_by(
        MCPLLMAxisScores.server_id,
        MCPLLMAxisScores.created_at.desc()
    ).all()

    # Process scores
    server_scores = {}
    for score in latest_scores:
        if score.server_id not in server_scores:
            server_scores[score.server_id] = score

    # Calculate metrics
    tier_distribution = {
        'TRUSTED_GENERAL': 0,
        'TRUSTED_RESEARCH': 0,
        'ENTERPRISE_CONTROLLED': 0,
        'CAUTION_LIMITED': 0,
        'HIGH_RISK_ISOLATED': 0,
        'INSUFFICIENT': 0
    }

    overall_risk_scores = []
    exploit_surface_scores = []
    recent_changes = 0
    stale_servers = 0

    for server in servers:
        score = server_scores.get(server.server_id)
        if score:
            # Update tier distribution
            tier_distribution[score.risk_tier] += 1

            # Collect scores for averages
            if score.overall_risk_p_top is not None:
                overall_risk_scores.append(score.overall_risk_p_top)
            if score.exploit_surface_p_top is not None:
                exploit_surface_scores.append(score.exploit_surface_p_top)

            # Check for recent changes (7 days)
            if (datetime.now() - score.created_at) <= timedelta(days=7):
                recent_changes += 1
        else:
            # No score means stale
            stale_servers += 1
            tier_distribution['INSUFFICIENT'] += 1

    # Calculate averages
    avg_overall_risk = sum(overall_risk_scores) / len(overall_risk_scores) if overall_risk_scores else None
    avg_exploit_surface = sum(exploit_surface_scores) / len(exploit_surface_scores) if exploit_surface_scores else None

    # Prepare server details if requested
    server_details = []
    if include_servers:
        for server in servers:
            score = server_scores.get(server.server_id)
            last_scored_ago = None
            if score:
                last_scored_ago = (datetime.now() - score.created_at).total_seconds()

            server_details.append(ServerRiskSummary(
                server_id=server.server_id,
                name=server.name,
                risk_tier=score.risk_tier if score else 'INSUFFICIENT',
                overall_risk_p_top=score.overall_risk_p_top if score else None,
                last_scored_ago_s=last_scored_ago
            ))

    return OrgFleetRiskSummary(
        org_id=org_id,
        total_servers=len(servers),
        tier_distribution=tier_distribution,
        avg_overall_risk_p_top=avg_overall_risk,
        avg_exploit_surface_p_top=avg_exploit_surface,
        recent_changes_7d=recent_changes,
        stale_servers_30d=stale_servers,
        servers=server_details if include_servers else None
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from datetime import datetime, timedelta

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    test_session = TestSession()
    test_session.add_all([
        MCPServerRegistry(
            server_id="server1",
            org_id="test_org",
            name="Server 1",
            created_at=datetime.now()
        ),
        MCPServerRegistry(
            server_id="server2",
            org_id="test_org",
            name="Server 2",
            created_at=datetime.now()
        ),
        MCPServerRegistry(
            server_id="server3",
            org_id="test_org",
            name="Server 3",
            created_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id="server1",
            overall_risk_p_top=0.1,
            exploit_surface_p_top=0.2,
            risk_tier="TRUSTED_GENERAL",
            created_at=datetime.now() - timedelta(days=1)
        ),
        MCPLLMAxisScores(
            server_id="server1",
            overall_risk_p_top=0.15,
            exploit_surface_p_top=0.25,
            risk_tier="TRUSTED_GENERAL",
            created_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id="server2",
            overall_risk_p_top=0.2,
            exploit_surface_p_top=0.3,
            risk_tier="TRUSTED_GENERAL",
            created_at=datetime.now() - timedelta(days=2)
        ),
        MCPLLMAxisScores(
            server_id="server3",
            overall_risk_p_top=0.8,
            exploit_surface_p_top=0.9,
            risk_tier="HIGH_RISK_ISOLATED",
            created_at=datetime.now() - timedelta(days=3)
        )
    ])
    test_session.commit()

    # Test endpoint
    client = TestClient(app)
    response = client.get("/orgs/test_org/fleet/risk_summary")
    assert response.status_code == 200
    data = response.json()

    # Verify results
    assert data["total_servers"] == 3
    assert sum(data["tier_distribution"].values()) == 3
    assert data["avg_overall_risk_p_top"] is not None

    print("PASS")