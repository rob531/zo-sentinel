from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import Server, TrustGatingOverride, MCPLLMAxisScores
from sqlalchemy.orm import Session
import httpx

router = APIRouter()

class RiskAxis(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool

class VerdictSummaryResponse(BaseModel):
    server_id: str
    axes: List[RiskAxis]
    overall_score: float
    published_overall_risk: float
    is_trusted: bool
    trust_gating_reason: Optional[str]
    risk_tier: str
    scored_at: str

def get_risk_tier(score: float) -> str:
    if score >= 75:
        return "TRUSTED_GENERAL"
    elif score >= 60:
        return "TRUSTED_RESEARCH"
    elif score >= 45:
        return "ENTERPRISE_CONTROLLED"
    elif score >= 30:
        return "CAUTION_LIMITED"
    elif score >= 15:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"

@router.get("/servers/{server_id}/verdict-summary", response_model=VerdictSummaryResponse)
async def get_verdict_summary(server_id: str, db: Session = Depends(get_session)):
    # Get server
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get trust gating override
    trust_override = db.query(TrustGatingOverride).filter(
        TrustGatingOverride.server_id == server_id
    ).first()

    # Get axis scores
    axis_scores = db.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id
    ).all()

    if not axis_scores:
        raise HTTPException(status_code=404, detail="No risk scores found for server")

    # Prepare axes data
    axes = []
    for axis in axis_scores:
        axes.append(RiskAxis(
            axis_name=axis.axis_name,
            label=axis.label,
            label_index=axis.label_index,
            p_top=axis.p_top,
            p_critical=axis.p_critical,
            p_danger=axis.p_danger,
            escalated=axis.escalated
        ))

    # Get overall score (from axis_scores[0] as it's the same for all)
    overall_score = axis_scores[0].overall_risk

    # Determine published overall risk
    published_overall_risk = trust_override.published_overall_risk if trust_override else overall_score

    # Determine trust status
    is_trusted = trust_override.trusted if trust_override else False
    trust_gating_reason = trust_override.reason if trust_override else None

    # Determine risk tier
    risk_tier = get_risk_tier(published_overall_risk)

    # Get scored_at (from axis_scores[0] as it's the same for all)
    scored_at = axis_scores[0].scored_at.isoformat()

    return VerdictSummaryResponse(
        server_id=server_id,
        axes=axes,
        overall_score=overall_score,
        published_overall_risk=published_overall_risk,
        is_trusted=is_trusted,
        trust_gating_reason=trust_gating_reason,
        risk_tier=risk_tier,
        scored_at=scored_at
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import SessionLocal
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Override dependencies for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Create test data
    test_server = Server(id="test-server-1")
    test_override = TrustGatingOverride(
        server_id="test-server-1",
        published_overall_risk=80.0,
        trusted=True,
        reason="Test override"
    )
    test_axis_scores = [
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="overall_risk",
            label="High",
            label_index=3,
            p_top=0.9,
            p_critical=0.8,
            p_danger=0.7,
            escalated=False,
            overall_risk=75.0,
            scored_at="2023-01-01T00:00:00"
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="auth_strength",
            label="Medium",
            label_index=2,
            p_top=0.7,
            p_critical=0.6,
            p_danger=0.5,
            escalated=False,
            overall_risk=75.0,
            scored_at="2023-01-01T00:00:00"
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="capability_breadth",
            label="High",
            label_index=3,
            p_top=0.8,
            p_critical=0.7,
            p_danger=0.6,
            escalated=False,
            overall_risk=75.0,
            scored_at="2023-01-01T00:00:00"
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="data_sensitivity",
            label="Low",
            label_index=1,
            p_top=0.5,
            p_critical=0.4,
            p_danger=0.3,
            escalated=False,
            overall_risk=75.0,
            scored_at="2023-01-01T00:00:00"
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="network_egress",
            label="Medium",
            label_index=2,
            p_top=0.6,
            p_critical=0.5,
            p_danger=0.4,
            escalated=False,
            overall_risk=75.0,
            scored_at="2023-01-01T00:00:00"
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="maintainer_trust",
            label="High",
            label_index=3,
            p_top=0.9,
            p_critical=0.8,
            p_danger=0.7,
            escalated=False,
            overall_risk=75.0,
            scored_at="2023-01-01T00:00:00"
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis_name="exploit_surface",
            label="Medium",
            label_index=2,
            p_top=0.7,
            p_critical=0.6,
            p_danger=0.5,
            escalated=False,
            overall_risk=75.0,
            scored_at="2023-01-01T00:00:00"
        )
    ]

    # Add test data to session
    db = TestSessionLocal()
    db.add(test_server)
    db.add(test_override)
    db.add_all(test_axis_scores)
    db.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/test-server-1/verdict-summary")
    assert response.status_code == 200
    data = response.json()

    # Verify response structure
    assert "server_id" in data
    assert "axes" in data
    assert len(data["axes"]) == 7
    assert "overall_score" in data
    assert "published_overall_risk" in data
    assert "is_trusted" in data
    assert "trust_gating_reason" in data
    assert "risk_tier" in data
    assert "scored_at" in data

    print("PASS")