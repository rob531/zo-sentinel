from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Org
from fastapi.testclient import TestClient
import uvicorn

router = APIRouter()

def get_org_id_from_session(session: Session) -> int:
    # In a real implementation, this would read from the user's session
    # For this example, we'll assume the first org in the database
    org = session.query(Org).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org.id

def get_servers_for_org(session: Session, org_id: int) -> List[MCPServerRegistry]:
    return session.query(MCPServerRegistry).filter(MCPServerRegistry.org_id == org_id).all()

def get_risk_tier(server: MCPServerRegistry, session: Session) -> str:
    # Calculate risk tier based on LLM axis scores
    scores = session.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server.id).all()
    if not scores:
        return "Unknown"

    total_score = sum(score.score for score in scores)
    avg_score = total_score / len(scores)

    if avg_score >= 80:
        return "High Risk"
    elif avg_score >= 50:
        return "Medium Risk"
    else:
        return "Low Risk"

def get_recent_verdict_changes(server: MCPServerRegistry, session: Session) -> List[Dict[str, Any]]:
    # Get recent verdict changes (last 5)
    disputes = session.query(MCPScoreDisputes).filter(
        MCPScoreDisputes.server_id == server.id
    ).order_by(MCPScoreDisputes.created_at.desc()).limit(5).all()

    return [{
        "id": dispute.id,
        "old_verdict": dispute.old_verdict,
        "new_verdict": dispute.new_verdict,
        "created_at": dispute.created_at
    } for dispute in disputes]

def get_open_dispute_count(server: MCPServerRegistry, session: Session) -> int:
    return session.query(MCPScoreDisputes).filter(
        MCPScoreDisputes.server_id == server.id,
        MCPScoreDisputes.resolved == False
    ).count()

@router.get("/org-health-dashboard", response_model=List[Dict[str, Any]])
async def get_org_health_dashboard(session: Session = Depends(get_session)):
    org_id = get_org_id_from_session(session)
    servers = get_servers_for_org(session, org_id)

    dashboard_data = []
    for server in servers:
        risk_tier = get_risk_tier(server, session)
        recent_changes = get_recent_verdict_changes(server, session)
        open_disputes = get_open_dispute_count(server, session)

        dashboard_data.append({
            "server_id": server.id,
            "server_name": server.name,
            "risk_tier": risk_tier,
            "recent_verdict_changes": recent_changes,
            "open_dispute_count": open_disputes
        })

    return dashboard_data

if __name__ == "__main__":
    from app.db import get_session, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    app.dependency_overrides[get_session] = lambda: TestingSession()

    # Create test data
    test_org = Org(name="Test Org")
    test_server = MCPServerRegistry(name="Test Server", org_id=1)
    test_score = MCPLLMAxisScores(server_id=1, score=75)
    test_dispute = MCPScoreDisputes(server_id=1, old_verdict="Good", new_verdict="Bad", resolved=False)

    session = TestingSession()
    session.add_all([test_org, test_server, test_score, test_dispute])
    session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/org-health-dashboard")

    assert response.status_code == 200
    assert len(response.json()) > 0
    assert "server_id" in response.json()[0]
    assert "risk_tier" in response.json()[0]
    assert "open_dispute_count" in response.json()[0]

    print("PASS")