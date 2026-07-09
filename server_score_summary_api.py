from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime
from typing import Dict, Optional
from app.db import get_session
from app.models import MCPLLMAxisScore
from sqlalchemy.orm import Session

router = APIRouter()

class AxisScore(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float

class OverallRisk(BaseModel):
    label: str
    p_top: float
    p_critical: float

class ScoreSummary(BaseModel):
    server_id: str
    axes: Dict[str, AxisScore]
    overall_risk: OverallRisk
    risk_tier: str
    criteria_version: str
    scored_at: str

def compute_risk_tier(p_top: float) -> str:
    if p_top > 75:
        return "TRUSTED_GENERAL"
    elif p_top > 60:
        return "TRUSTED_RESEARCH"
    elif p_top > 45:
        return "ENTERPRISE_CONTROLLED"
    elif p_top > 30:
        return "CAUTION_LIMITED"
    elif p_top > 15:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"

@router.get("/servers/{server_id}/score-summary", response_model=ScoreSummary)
async def get_server_score_summary(server_id: str, db: Session = Depends(get_session)):
    score = db.query(MCPLLMAxisScore).filter(
        MCPLLMAxisScore.server_id == server_id
    ).order_by(MCPLLMAxisScore.scored_at.desc()).first()

    if not score:
        raise HTTPException(status_code=404, detail="Server score not found")

    axes = {
        "privacy": AxisScore(
            label="Privacy",
            p_top=score.privacy_p_top,
            p_critical=score.privacy_p_critical,
            p_danger=score.privacy_p_danger
        ),
        "security": AxisScore(
            label="Security",
            p_top=score.security_p_top,
            p_critical=score.security_p_critical,
            p_danger=score.security_p_danger
        ),
        "reliability": AxisScore(
            label="Reliability",
            p_top=score.reliability_p_top,
            p_critical=score.reliability_p_critical,
            p_danger=score.reliability_p_danger
        ),
        "ethics": AxisScore(
            label="Ethics",
            p_top=score.ethics_p_top,
            p_critical=score.ethics_p_critical,
            p_danger=score.ethics_p_danger
        ),
        "compliance": AxisScore(
            label="Compliance",
            p_top=score.compliance_p_top,
            p_critical=score.compliance_p_critical,
            p_danger=score.compliance_p_danger
        ),
        "performance": AxisScore(
            label="Performance",
            p_top=score.performance_p_top,
            p_critical=score.performance_p_critical,
            p_danger=score.performance_p_danger
        )
    }

    overall_risk = OverallRisk(
        label="Overall Risk",
        p_top=score.overall_risk_p_top,
        p_critical=score.overall_risk_p_critical
    )

    risk_tier = compute_risk_tier(score.overall_risk_p_top)

    return ScoreSummary(
        server_id=server_id,
        axes=axes,
        overall_risk=overall_risk,
        risk_tier=risk_tier,
        criteria_version=score.criteria_version,
        scored_at=score.scored_at.isoformat()
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import SessionLocal
    from app.models import MCPLLMAxisScore, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Seed test data
    test_session = SessionLocal()
    test_session.add_all([
        MCPLLMAxisScore(
            server_id="test-id",
            privacy_p_top=0.8,
            privacy_p_critical=0.7,
            privacy_p_danger=0.6,
            security_p_top=0.7,
            security_p_critical=0.6,
            security_p_danger=0.5,
            reliability_p_top=0.6,
            reliability_p_critical=0.5,
            reliability_p_danger=0.4,
            ethics_p_top=0.5,
            ethics_p_critical=0.4,
            ethics_p_danger=0.3,
            compliance_p_top=0.4,
            compliance_p_critical=0.3,
            compliance_p_danger=0.2,
            performance_p_top=0.3,
            performance_p_critical=0.2,
            performance_p_danger=0.1,
            overall_risk_p_top=0.5,
            overall_risk_p_critical=0.4,
            criteria_version="1.0",
            scored_at=datetime.now()
        )
    ])
    test_session.commit()

    # Override dependency for testing
    from app import app
    app.dependency_overrides[get_session] = lambda: test_session

    # Test
    client = TestClient(app)
    response = client.get("/servers/test-id/score-summary")
    assert response.status_code == 200
    data = response.json()
    assert "server_id" in data
    assert "axes" in data
    assert len(data["axes"]) == 6
    assert "overall_risk" in data
    assert "risk_tier" in data
    assert "criteria_version" in data
    assert "scored_at" in data
    print("PASS")