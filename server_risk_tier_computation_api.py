from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional
from datetime import datetime
from app.db import get_session
from app.models import McpLlmAxisScores, McpServerRegistry, McpRiskRegister
from sqlalchemy.orm import Session
import requests
from fastapi.testclient import TestClient

router = APIRouter()

class AxisScore(BaseModel):
    label: str
    p_top: float

class RiskTierComputationResponse(BaseModel):
    server_id: str
    server_name: str
    computed_score: float
    computed_tier: str
    criteria_version: str
    computed_at: str
    axes: Dict[str, AxisScore]

class ComputeRiskTierRequest(BaseModel):
    recompute: bool

def get_axis_scores(db: Session, server_id: str) -> Dict[str, AxisScore]:
    axis_names = [
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface"
    ]

    scores = {}
    for axis in axis_names:
        score = db.query(McpLlmAxisScores).filter(
            McpLlmAxisScores.server_id == server_id,
            McpLlmAxisScores.axis_name == axis
        ).order_by(McpLlmAxisScores.scored_at.desc()).first()

        if score:
            scores[axis] = AxisScore(
                label=score.label,
                p_top=score.p_top
            )
        else:
            scores[axis] = AxisScore(
                label="UNKNOWN",
                p_top=0.0
            )

    return scores

def compute_risk_tier(axes: Dict[str, AxisScore]) -> str:
    if any(axis.label == "CRITICAL" for axis in axes.values()):
        return "CRITICAL"

    score = axes["overall_risk"].p_top * 0.40
    for axis in ["auth_strength", "capability_breadth", "data_sensitivity",
                 "network_egress", "maintainer_trust", "exploit_surface"]:
        score += axes[axis].p_top * 0.10

    if score >= 75:
        return "LOW"
    elif score >= 50:
        return "MEDIUM"
    elif score >= 25:
        return "HIGH"
    else:
        return "CRITICAL"

def write_risk_register(db: Session, server_id: str, computed_score: float,
                       computed_tier: str, axes: Dict[str, AxisScore]):
    risk_register = McpRiskRegister(
        server_id=server_id,
        computed_score=computed_score,
        risk_tier=computed_tier,
        axes=axes,
        computed_at=datetime.utcnow()
    )

    db.merge(risk_register)
    db.commit()

@router.post("/servers/{server_id}/compute-risk-tier", response_model=RiskTierComputationResponse)
async def compute_risk_tier_endpoint(
    server_id: str,
    request: ComputeRiskTierRequest,
    db: Session = Depends(get_session)
):
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    axes = get_axis_scores(db, server_id)
    computed_tier = compute_risk_tier(axes)

    overall_risk = axes["overall_risk"].p_top
    other_axes = [axes[axis].p_top for axis in ["auth_strength", "capability_breadth",
                                               "data_sensitivity", "network_egress",
                                               "maintainer_trust", "exploit_surface"]]
    computed_score = overall_risk * 0.40 + sum(other_axes) * 0.10

    if request.recompute:
        write_risk_register(db, server_id, computed_score, computed_tier, axes)

    return RiskTierComputationResponse(
        server_id=server_id,
        server_name=server.name,
        computed_score=computed_score,
        computed_tier=computed_tier,
        criteria_version="v1.0",
        computed_at=datetime.utcnow().isoformat(),
        axes=axes
    )

@router.get("/servers/{server_id}/risk-tier", response_model=RiskTierComputationResponse)
async def get_risk_tier_endpoint(
    server_id: str,
    db: Session = Depends(get_session)
):
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    risk_register = db.query(McpRiskRegister).filter(McpRiskRegister.server_id == server_id).first()
    if not risk_register:
        raise HTTPException(status_code=404, detail="Risk tier not computed yet")

    return RiskTierComputationResponse(
        server_id=server_id,
        server_name=server.name,
        computed_score=risk_register.computed_score,
        computed_tier=risk_register.risk_tier,
        criteria_version="v1.0",
        computed_at=risk_register.computed_at.isoformat(),
        axes=risk_register.axes
    )

if __name__ == '__main__':
    from fastapi import FastAPI
    from app.db import get_session
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    # Override the database session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed test data
    session = TestSession()
    test_server = McpServerRegistry(server_id="test-server-1", name="Test Server")
    session.add(test_server)

    axis_scores = [
        McpLlmAxisScores(server_id="test-server-1", axis_name="overall_risk", label="MEDIUM", p_top=60.0),
        McpLlmAxisScores(server_id="test-server-1", axis_name="auth_strength", label="LOW", p_top=80.0),
        McpLlmAxisScores(server_id="test-server-1", axis_name="capability_breadth", label="MEDIUM", p_top=50.0),
        McpLlmAxisScores(server_id="test-server-1", axis_name="data_sensitivity", label="HIGH", p_top=30.0),
        McpLlmAxisScores(server_id="test-server-1", axis_name="network_egress", label="LOW", p_top=85.0),
        McpLlmAxisScores(server_id="test-server-1", axis_name="maintainer_trust", label="MEDIUM", p_top=55.0),
        McpLlmAxisScores(server_id="test-server-1", axis_name="exploit_surface", label="LOW", p_top=90.0)
    ]
    session.add_all(axis_scores)
    session.commit()

    client = TestClient(app)
    response = client.post("/servers/test-server-1/compute-risk-tier", json={"recompute": True})

    assert response.status_code == 200
    data = response.json()
    assert data["computed_tier"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert isinstance(data["computed_score"], float) and 0 <= data["computed_score"] <= 100
    assert all(axis in data["axes"] for axis in ["overall_risk", "auth_strength", "capability_breadth",
                                                "data_sensitivity", "network_egress", "maintainer_trust",
                                                "exploit_surface"])

    print("PASS")