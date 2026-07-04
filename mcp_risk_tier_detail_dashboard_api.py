from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Dict, List

from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Orgs, Users

router = APIRouter()

class ServerDetail(BaseModel):
    server_id: str
    org_id: str
    org_name: str
    user_id: str
    user_name: str
    llm_axis_scores: Dict[str, float]
    disputes: List[Dict[str, str]]

class RiskTierDetail(BaseModel):
    tier: str
    servers: List[ServerDetail]

@router.get("/risk-tier-detail", response_model=RiskTierDetail)
async def get_risk_tier_detail(tier: str, db: Session = Depends(get_session)) -> RiskTierDetail:
    servers = db.query(MCPServerRegistry).filter(MCPServerRegistry.risk_tier == tier).all()

    server_details = []
    for server in servers:
        llm_scores = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server.server_id).first()
        disputes = db.query(MCPScoreDisputes).filter(MCPScoreDisputes.server_id == server.server_id).all()

        org = db.query(Orgs).filter(Orgs.org_id == server.org_id).first()
        user = db.query(Users).filter(Users.user_id == server.user_id).first()

        server_details.append(ServerDetail(
            server_id=server.server_id,
            org_id=server.org_id,
            org_name=org.org_name if org else None,
            user_id=server.user_id,
            user_name=user.user_name if user else None,
            llm_axis_scores={axis: getattr(llm_scores, axis) for axis in llm_scores.__table__.columns.keys() if axis != 'server_id'},
            disputes=[{
                'dispute_id': dispute.dispute_id,
                'score_type': dispute.score_type,
                'disputed_score': dispute.disputed_score,
                'new_score': dispute.new_score,
                'reason': dispute.reason
            } for dispute in disputes]
        ))

    return RiskTierDetail(tier=tier, servers=server_details)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_session = TestSession()
    test_org = Orgs(org_id="org1", org_name="Test Org")
    test_user = Users(user_id="user1", user_name="Test User")
    test_server = MCPServerRegistry(
        server_id="server1",
        org_id="org1",
        user_id="user1",
        risk_tier="high"
    )
    test_llm_scores = MCPLLMAxisScores(
        server_id="server1",
        axis1=0.8,
        axis2=0.6,
        axis3=0.4
    )
    test_dispute = MCPScoreDisputes(
        dispute_id="dispute1",
        server_id="server1",
        score_type="axis1",
        disputed_score=0.8,
        new_score=0.7,
        reason="Test reason"
    )

    test_session.add_all([test_org, test_user, test_server, test_llm_scores, test_dispute])
    test_session.commit()

    # Test the endpoint
    client = TestClient(router)
    response = client.get("/risk-tier-detail?tier=high")
    assert response.status_code == 200
    assert response.json() == {
        "tier": "high",
        "servers": [{
            "server_id": "server1",
            "org_id": "org1",
            "org_name": "Test Org",
            "user_id": "user1",
            "user_name": "Test User",
            "llm_axis_scores": {"axis1": 0.8, "axis2": 0.6, "axis3": 0.4},
            "disputes": [{
                "dispute_id": "dispute1",
                "score_type": "axis1",
                "disputed_score": 0.8,
                "new_score": 0.7,
                "reason": "Test reason"
            }]
        }]
    }

    print("PASS")