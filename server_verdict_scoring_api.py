from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional
from app.db import get_session
from app.models import MCPLlmAxisScore, MCPServerRegistry
from sqlalchemy.orm import Session
from app.services.trust_gating_override import trust_gate

router = APIRouter()

class AxisScore(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float

class ServerVerdictResponse(BaseModel):
    server_id: str
    name: str
    url: str
    trust_score: float
    axes: Dict[str, AxisScore]
    overall: float
    risk_tier: str
    criteria_version: str
    published_overall_risk: Optional[float] = None
    trusted: Optional[bool] = None

class ServerScoreResponse(BaseModel):
    server_id: str
    axes: Dict[str, AxisScore]
    overall: float
    risk_tier: str
    criteria_version: str
    published_overall_risk: Optional[float] = None
    trusted: Optional[bool] = None

def calculate_risk_tier(overall: float) -> str:
    if overall >= 0.8:
        return "Low"
    elif overall >= 0.6:
        return "Medium"
    elif overall >= 0.4:
        return "High"
    else:
        return "Critical"

def get_axis_scores(db: Session, server_id: str) -> Dict[str, AxisScore]:
    axis_names = [
        "overall_risk", "auth_strength", "capability_breadth",
        "data_sensitivity", "network_egress", "maintainer_trust",
        "exploit_surface"
    ]
    axis_scores = {}
    for axis_name in axis_names:
        score = db.query(MCPLlmAxisScore).filter(
            MCPLlmAxisScore.server_id == server_id,
            MCPLlmAxisScore.axis_name == axis_name
        ).first()
        if score:
            axis_scores[axis_name] = AxisScore(
                label=score.axis_name,
                p_top=score.p_top,
                p_critical=score.p_critical,
                p_danger=score.p_danger
            )
    return axis_scores

@router.get("/servers/{server_id}/risk-verdict", response_model=ServerVerdictResponse)
async def get_risk_verdict(
    server_id: str,
    include_trust_gate: bool = False,
    db: Session = Depends(get_session)
):
    server = db.query(MCPServerRegistry).filter(
        MCPServerRegistry.server_id == server_id
    ).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    axes = get_axis_scores(db, server_id)
    if not axes:
        raise HTTPException(status_code=404, detail="No axis scores found for server")

    overall = axes["overall_risk"].p_top
    risk_tier = calculate_risk_tier(overall)

    response = ServerVerdictResponse(
        server_id=server.server_id,
        name=server.name,
        url=server.url,
        trust_score=server.trust_score,
        axes=axes,
        overall=overall,
        risk_tier=risk_tier,
        criteria_version="1.0"
    )

    if include_trust_gate:
        published_overall_risk, trusted = trust_gate(server_id)
        response.published_overall_risk = published_overall_risk
        response.trusted = trusted

    return response

@router.get("/servers/{server_id}/verdict-score", response_model=ServerScoreResponse)
async def get_verdict_score(
    server_id: str,
    include_trust_gate: bool = False,
    db: Session = Depends(get_session)
):
    server = db.query(MCPServerRegistry).filter(
        MCPServerRegistry.server_id == server_id
    ).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    axes = get_axis_scores(db, server_id)
    if not axes:
        raise HTTPException(status_code=404, detail="No axis scores found for server")

    overall = axes["overall_risk"].p_top
    risk_tier = calculate_risk_tier(overall)

    response = ServerScoreResponse(
        server_id=server.server_id,
        axes=axes,
        overall=overall,
        risk_tier=risk_tier,
        criteria_version="1.0"
    )

    if include_trust_gate:
        published_overall_risk, trusted = trust_gate(server_id)
        response.published_overall_risk = published_overall_risk
        response.trusted = trusted

    return response

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # In-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    Base.metadata.create_all(test_engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    test_server_id = "test-001"
    test_server = MCPServerRegistry(
        server_id=test_server_id,
        name="Test Server",
        url="http://test.example.com",
        trust_score=0.85
    )

    axis_scores = [
        MCPLlmAxisScore(
            server_id=test_server_id,
            axis_name="overall_risk",
            p_top=0.75,
            p_critical=0.2,
            p_danger=0.05
        ),
        MCPLlmAxisScore(
            server_id=test_server_id,
            axis_name="auth_strength",
            p_top=0.8,
            p_critical=0.15,
            p_danger=0.05
        ),
        MCPLlmAxisScore(
            server_id=test_server_id,
            axis_name="capability_breadth",
            p_top=0.7,
            p_critical=0.2,
            p_danger=0.1
        ),
        MCPLlmAxisScore(
            server_id=test_server_id,
            axis_name="data_sensitivity",
            p_top=0.65,
            p_critical=0.25,
            p_danger=0.1
        ),
        MCPLlmAxisScore(
            server_id=test_server_id,
            axis_name="network_egress",
            p_top=0.85,
            p_critical=0.1,
            p_danger=0.05
        ),
        MCPLlmAxisScore(
            server_id=test_server_id,
            axis_name="maintainer_trust",
            p_top=0.9,
            p_critical=0.05,
            p_danger=0.05
        ),
        MCPLlmAxisScore(
            server_id=test_server_id,
            axis_name="exploit_surface",
            p_top=0.6,
            p_critical=0.3,
            p_danger=0.1
        )
    ]

    with TestSession() as session:
        session.add(test_server)
        session.add_all(axis_scores)
        session.commit()

    client = TestClient(app)

    # Test risk-verdict endpoint
    response = client.get(f"/servers/{test_server_id}/risk-verdict")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == test_server_id
    assert len(data["axes"]) == 7
    assert data["risk_tier"] in ["Low", "Medium", "High", "Critical"]

    # Test verdict-score endpoint
    response = client.get(f"/servers/{test_server_id}/verdict-score")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == test_server_id
    assert len(data["axes"]) == 7
    assert data["risk_tier"] in ["Low", "Medium", "High", "Critical"]

    print("PASS")