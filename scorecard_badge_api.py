from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScores

router = APIRouter()

class AxisScore(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    tier: str

class RiskScorecardBadge(BaseModel):
    server_id: str
    risk_tier: str
    overall_score: float
    axes: List[AxisScore]

class CompactRiskScorecardBadge(BaseModel):
    server_id: str
    risk_tier: str
    overall_score: float

def calculate_overall_score(axes: List[AxisScore]) -> float:
    weights = {
        'security': 0.2,
        'performance': 0.15,
        'reliability': 0.2,
        'maintainability': 0.15,
        'usability': 0.15,
        'compliance': 0.15
    }
    weighted_sum = sum(axes[i].p_top * weights[axes[i].axis_name] for i in range(len(axes)))
    return round(weighted_sum * 100, 2)

def determine_risk_tier(overall_score: float, axes: List[AxisScore]) -> str:
    if any(axis.p_critical > 0.7 for axis in axes):
        return "HIGH_RISK_ISOLATED"
    if overall_score >= 90:
        return "LOW_RISK"
    elif overall_score >= 70:
        return "MEDIUM_RISK"
    elif overall_score >= 50:
        return "HIGH_RISK"
    else:
        return "CRITICAL_RISK"

@router.get("/servers/{server_id}/badge", response_model=RiskScorecardBadge)
async def get_risk_scorecard_badge(server_id: str, session: Session = Depends(get_session)):
    axis_scores = session.query(McpLlmAxisScores).filter(McpLlmAxisScores.server_id == server_id).all()

    if not axis_scores:
        raise HTTPException(status_code=404, detail="Server not found")

    axes = []
    for score in axis_scores:
        axes.append(AxisScore(
            axis_name=score.axis_name,
            label=score.label,
            p_top=score.p_top,
            p_critical=score.p_critical,
            p_danger=score.p_danger,
            tier=score.tier
        ))

    overall_score = calculate_overall_score(axes)
    risk_tier = determine_risk_tier(overall_score, axes)

    return RiskScorecardBadge(
        server_id=server_id,
        risk_tier=risk_tier,
        overall_score=overall_score,
        axes=axes
    )

@router.get("/servers/{server_id}/badge/compact", response_model=CompactRiskScorecardBadge)
async def get_compact_risk_scorecard_badge(server_id: str, session: Session = Depends(get_session)):
    axis_scores = session.query(McpLlmAxisScores).filter(McpLlmAxisScores.server_id == server_id).all()

    if not axis_scores:
        raise HTTPException(status_code=404, detail="Server not found")

    axes = []
    for score in axis_scores:
        axes.append(AxisScore(
            axis_name=score.axis_name,
            label=score.label,
            p_top=score.p_top,
            p_critical=score.p_critical,
            p_danger=score.p_danger,
            tier=score.tier
        ))

    overall_score = calculate_overall_score(axes)
    risk_tier = determine_risk_tier(overall_score, axes)

    return CompactRiskScorecardBadge(
        server_id=server_id,
        risk_tier=risk_tier,
        overall_score=overall_score
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpLlmAxisScores
    from sqlalchemy.orm import sessionmaker

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    test_app = FastAPI()
    test_app.include_router(router)

    client = TestClient(test_app)

    # Seed test data
    test_session = SessionLocal()
    test_server_id = "test_server_123"

    test_data = [
        McpLlmAxisScores(
            server_id=test_server_id,
            axis_name="security",
            label="Security",
            p_top=0.9,
            p_critical=0.1,
            p_danger=0.05,
            tier="LOW_RISK"
        ),
        McpLlmAxisScores(
            server_id=test_server_id,
            axis_name="performance",
            label="Performance",
            p_top=0.8,
            p_critical=0.2,
            p_danger=0.1,
            tier="MEDIUM_RISK"
        ),
        McpLlmAxisScores(
            server_id=test_server_id,
            axis_name="reliability",
            label="Reliability",
            p_top=0.7,
            p_critical=0.3,
            p_danger=0.2,
            tier="HIGH_RISK"
        ),
        McpLlmAxisScores(
            server_id=test_server_id,
            axis_name="maintainability",
            label="Maintainability",
            p_top=0.85,
            p_critical=0.15,
            p_danger=0.1,
            tier="MEDIUM_RISK"
        ),
        McpLlmAxisScores(
            server_id=test_server_id,
            axis_name="usability",
            label="Usability",
            p_top=0.75,
            p_critical=0.25,
            p_danger=0.15,
            tier="HIGH_RISK"
        ),
        McpLlmAxisScores(
            server_id=test_server_id,
            axis_name="compliance",
            label="Compliance",
            p_top=0.95,
            p_critical=0.05,
            p_danger=0.02,
            tier="LOW_RISK"
        ),
        McpLlmAxisScores(
            server_id=test_server_id,
            axis_name="overall_risk",
            label="Overall Risk",
            p_top=0.8,
            p_critical=0.2,
            p_danger=0.1,
            tier="MEDIUM_RISK"
        )
    ]

    test_session.add_all(test_data)
    test_session.commit()

    # Test /badge endpoint
    response = client.get(f"/servers/{test_server_id}/badge")
    assert response.status_code == 200
    data = response.json()
    assert len(data["axes"]) == 7
    assert all(axis["p_top"] is not None for axis in data["axes"])
    assert data["server_id"] == test_server_id
    assert "overall_score" in data
    assert "risk_tier" in data

    # Test /badge/compact endpoint
    response = client.get(f"/servers/{test_server_id}/badge/compact")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == test_server_id
    assert "overall_score" in data
    assert "risk_tier" in data
    assert "axes" not in data

    # Test CRITICAL-axis override
    test_session.query(McpLlmAxisScores).filter(McpLlmAxisScores.server_id == test_server_id).update({"p_critical": 0.8})
    test_session.commit()

    response = client.get(f"/servers/{test_server_id}/badge")
    assert response.status_code == 200
    data = response.json()
    assert data["risk_tier"] == "HIGH_RISK_ISOLATED"

    print("PASS")