from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session
import requests

router = APIRouter()

class AxisFinding(BaseModel):
    axis: str
    label: str
    probability: float
    p_top: float

class VerdictFindingsResponse(BaseModel):
    server_id: str
    risk_tier: str
    verdict: str
    verdict_reasoning: str
    confidence: float
    axes: List[AxisFinding]
    derived_trust: str
    criteria_version: str = "v1.0"
    scored_at: datetime

def trust_gating_override(url: str, name: str, axes_dict: dict) -> str:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/trust_gate",
            json={"url": url, "name": name, "axes": axes_dict},
            timeout=5
        )
        response.raise_for_status()
        return response.json().get("trust_gate", "untrusted")
    except requests.RequestException:
        return "untrusted"

@router.get("/servers/{server_id}/verdict-findings", response_model=VerdictFindingsResponse)
async def get_verdict_findings(server_id: str, db: Session = Depends(get_session)):
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    axis_scores = db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()

    if not axis_scores:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Axis scores not found")

    axes = []
    axes_dict = {}
    scored_at = None

    for score in axis_scores:
        if score.scored_at > scored_at:
            scored_at = score.scored_at
        axes.append(AxisFinding(
            axis=score.axis,
            label=score.label,
            probability=score.probability,
            p_top=score.p_top
        ))
        axes_dict[score.axis] = {
            "label": score.label,
            "probability": score.probability,
            "p_top": score.p_top
        }

    derived_trust = trust_gating_override(server.url, server.name, axes_dict)

    return VerdictFindingsResponse(
        server_id=server.server_id,
        risk_tier=server.risk_tier,
        verdict=server.verdict,
        verdict_reasoning=server.verdict_reasoning,
        confidence=server.confidence,
        axes=axes,
        derived_trust=derived_trust,
        scored_at=scored_at
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    test_server = MCPServerRegistry(
        server_id="test-server-1",
        url="http://test.com",
        name="Test Server",
        risk_tier="high",
        verdict="malicious",
        verdict_reasoning="Test reasoning",
        confidence=0.95
    )

    test_axis_scores = [
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis="overall_risk",
            label="high",
            probability=0.9,
            p_top=0.95,
            scored_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis="auth_strength",
            label="low",
            probability=0.2,
            p_top=0.25,
            scored_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis="capability_breadth",
            label="medium",
            probability=0.5,
            p_top=0.55,
            scored_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis="data_sensitivity",
            label="high",
            probability=0.8,
            p_top=0.85,
            scored_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis="network_egress",
            label="medium",
            probability=0.6,
            p_top=0.65,
            scored_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis="maintainer_trust",
            label="low",
            probability=0.3,
            p_top=0.35,
            scored_at=datetime.now()
        ),
        MCPLLMAxisScores(
            server_id="test-server-1",
            axis="exploit_surface",
            label="high",
            probability=0.9,
            p_top=0.95,
            scored_at=datetime.now()
        )
    ]

    with SessionLocal() as session:
        session.add(test_server)
        session.add_all(test_axis_scores)
        session.commit()

    client = TestClient(app)

    response = client.get("/servers/test-server-1/verdict-findings")
    assert response.status_code == 200
    data = response.json()
    assert len(data["axes"]) == 7
    assert data["risk_tier"] == "high"
    assert data["derived_trust"] in ["trusted", "untrusted"]

    response = client.get("/servers/nonexistent/verdict-findings")
    assert response.status_code == 404

    print("PASS")