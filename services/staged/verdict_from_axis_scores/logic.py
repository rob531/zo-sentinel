from typing import Dict, List, Optional
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from pydantic import BaseModel

class AxisDetails(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    probs: Dict[str, float]
    escalated: bool

class VerdictResponse(BaseModel):
    server_id: str
    risk_tier: str
    verdict: str
    axes: Dict[str, AxisDetails]
    criteria_version: str

def get_verdict_from_axis_scores(server_id: str, session: Session = Depends(get_session)) -> VerdictResponse:
    # Query the database for the server and its axis scores
    server = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    axis_scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()

    if not axis_scores:
        raise HTTPException(status_code=404, detail="Axis scores not found for server")

    # Initialize axes dictionary with default values
    axes: Dict[str, AxisDetails] = {
        "auth_strength": AxisDetails(
            label="Authentication Strength",
            p_top=0.0,
            p_critical=0.0,
            p_danger=0.0,
            probs={},
            escalated=False
        ),
        "capability_breadth": AxisDetails(
            label="Capability Breadth",
            p_top=0.0,
            p_critical=0.0,
            p_danger=0.0,
            probs={},
            escalated=False
        ),
        "data_sensitivity": AxisDetails(
            label="Data Sensitivity",
            p_top=0.0,
            p_critical=0.0,
            p_danger=0.0,
            probs={},
            escalated=False
        ),
        "network_egress": AxisDetails(
            label="Network Egress",
            p_top=0.0,
            p_critical=0.0,
            p_danger=0.0,
            probs={},
            escalated=False
        ),
        "maintainer_trust": AxisDetails(
            label="Maintainer Trust",
            p_top=0.0,
            p_critical=0.0,
            p_danger=0.0,
            probs={},
            escalated=False
        ),
        "exploit_surface": AxisDetails(
            label="Exploit Surface",
            p_top=0.0,
            p_critical=0.0,
            p_danger=0.0,
            probs={},
            escalated=False
        ),
        "overall_risk": AxisDetails(
            label="Overall Risk",
            p_top=0.0,
            p_critical=0.0,
            p_danger=0.0,
            probs={},
            escalated=False
        )
    }

    # Populate axes with actual data
    for score in axis_scores:
        if score.axis_name in axes:
            axes[score.axis_name] = AxisDetails(
                label=score.axis_name.replace("_", " ").title(),
                p_top=score.p_top,
                p_critical=score.p_critical,
                p_danger=score.p_danger,
                probs=score.probs,
                escalated=score.escalated
            )

    # Determine the risk tier based on overall_risk
    overall_risk = axes["overall_risk"].p_top
    if overall_risk > 75:
        risk_tier = "TRUSTED_GENERAL"
        verdict = "Trusted General"
    elif overall_risk > 60:
        risk_tier = "TRUSTED_RESEARCH"
        verdict = "Trusted Research"
    elif overall_risk > 45:
        risk_tier = "ENTERPRISE_CONTROLLED"
        verdict = "Enterprise Controlled"
    elif overall_risk > 30:
        risk_tier = "CAUTION_LIMITED"
        verdict = "Caution Limited"
    elif overall_risk > 15:
        risk_tier = "HIGH_RISK_ISOLATED"
        verdict = "High Risk Isolated"
    else:
        risk_tier = "KNOWN_THREAT"
        verdict = "Known Threat"

    return VerdictResponse(
        server_id=server_id,
        risk_tier=risk_tier,
        verdict=verdict,
        axes=axes,
        criteria_version="v1.0"
    )

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    from app.db import get_session
    from fastapi import Depends
    from fastapi.testclient import TestClient
    from main import app

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed the database with test data
    test_data = [
        {
            "server_id": "server1",
            "axis_name": "auth_strength",
            "p_top": 80.0,
            "p_critical": 10.0,
            "p_danger": 5.0,
            "probs": {"high": 0.8, "medium": 0.1, "low": 0.1},
            "escalated": False
        },
        {
            "server_id": "server1",
            "axis_name": "capability_breadth",
            "p_top": 70.0,
            "p_critical": 15.0,
            "p_danger": 10.0,
            "probs": {"high": 0.7, "medium": 0.2, "low": 0.1},
            "escalated": False
        },
        {
            "server_id": "server1",
            "axis_name": "data_sensitivity",
            "p_top": 65.0,
            "p_critical": 20.0,
            "p_danger": 10.0,
            "probs": {"high": 0.65, "medium": 0.2, "low": 0.15},
            "escalated": False
        },
        {
            "server_id": "server1",
            "axis_name": "network_egress",
            "p_top": 50.0,
            "p_critical": 25.0,
            "p_danger": 15.0,
            "probs": {"high": 0.5, "medium": 0.3, "low": 0.2},
            "escalated": False
        },
        {
            "server_id": "server1",
            "axis_name": "maintainer_trust",
            "p_top": 40.0,
            "p_critical": 30.0,
            "p_danger": 20.0,
            "probs": {"high": 0.4, "medium": 0.35, "low": 0.25},
            "escalated": False
        },
        {
            "server_id": "server1",
            "axis_name": "exploit_surface",
            "p_top": 35.0,
            "p_critical": 35.0,
            "p_danger": 25.0,
            "probs": {"high": 0.35, "medium": 0.4, "low": 0.25},
            "escalated": False
        },
        {
            "server_id": "server1",
            "axis_name": "overall_risk",
            "p_top": 85.0,
            "p_critical": 10.0,
            "p_danger": 5.0,
            "probs": {"high": 0.85, "medium": 0.1, "low": 0.05},
            "escalated": False
        },
        {
            "server_id": "server2",
            "axis_name": "auth_strength",
            "p_top": 65.0,
            "p_critical": 20.0,
            "p_danger": 10.0,
            "probs": {"high": 0.65, "medium": 0.2, "low": 0.15},
            "escalated": False
        },
        {
            "server_id": "server2",
            "axis_name": "capability_breadth",
            "p_top": 55.0,
            "p_critical": 25.0,
            "p_danger": 15.0,
            "probs": {"high": 0.55, "medium": 0.3, "low": 0.15},
            "escalated": False
        },
        {
            "server_id": "server2",
            "axis_name": "data_sensitivity",
            "p_top": 50.0,
            "p_critical": 30.0,
            "p_danger": 15.0,
            "probs": {"high": 0.5, "medium": 0.35, "low": 0.15},
            "escalated": False
        },
        {
            "server_id": "server2",
            "axis_name": "network_egress",
            "p_top": 45.0,
            "p_critical": 35.0,
            "p_danger": 20.0,
            "probs": {"high": 0.45, "medium": 0.4, "low": 0.15},
            "escalated": False
        },
        {
            "server_id": "server2",
            "axis_name": "maintainer_trust",
            "p_top": 40.0,
            "p_critical": 40.0,
            "p_danger": 20.0,
            "probs": {"high": 0.4, "medium": 0.45, "low": 0.15},
            "escalated": False
        },
        {
            "server_id": "server2",
            "axis_name": "exploit_surface",
            "p_top": 35.0,
            "p_critical": 45.0,
            "p_danger": 20.0,
            "probs": {"high": 0.35, "medium": 0.5, "low": 0.15},
            "escalated": False
        },
        {
            "server_id": "server2",
            "axis_name": "overall_risk",
            "p_top": 65.0,
            "p_critical": 20.0,
            "p_danger": 10.0,
            "probs": {"high": 0.65, "medium": 0.2, "low": 0.15},
            "escalated": False
        },
        {
            "server_id": "server3",
            "axis_name": "auth_strength",
            "p_top": 50.0,
            "p_critical": 30.0,
            "p_danger": 15.0,
            "probs": {"high": 0.5, "medium": 0.35, "low": 0.15},
            "escalated": False
        },
        {
            "server_id": "server3",
            "axis_name": "capability_breadth",
            "p_top": 45.0,
            "p_critical": 35.0,
            "p_danger": 20.0,
            "probs": {"high": 0.45, "medium": 0.4, "low": 0.15},
            "escalated": False
        },
        {
            "server_id": "server3",
            "axis_name": "data_sensitivity",
            "p_top": 40.0,
            "p_critical": 40.0,
            "p_danger": 20.0,
            "probs": {"high": 0.4, "medium": 0.45, "low": 0.15},
            "escalated": False
        },
        {
            "server_id": "server3",
            "axis_name": "network_egress",
            "p_top": 35.0,
            "p_critical": 45.0,
            "p_danger": 20.0,
            "probs": {"high": 0.35, "medium": 0.5, "low": 0.15},
            "escalated": False
        },
        {
            "server_id": "server3",
            "axis_name": "maintainer_trust",
            "p_top": 30.0,
            "p_critical": 50.0,
            "p_danger": 20.0,
            "probs": {"high": 0.3, "medium": 0.55, "low": 0.15},
            "escalated": False
        },
        {
            "server_id": "server3",
            "axis_name": "exploit_surface",
            "p_top": 25.0,
            "p_critical": 55.0,
            "p_danger": 20.0,
            "probs": {"high": 0.25, "medium": 0.6, "low": 0.15},
            "escalated": False
        },
        {
            "server_id": "server3",
            "axis_name": "overall_risk",
            "p_top": 50.0,
            "p_critical": 30.0,
            "p_danger": 15.0,
            "probs": {"high": 0.5, "medium": 0.35, "low": 0.15},
            "escalated": False
        }
    ]

    for data in test_data:
        score = McpLlmAxisScore(**data)
        session = SessionLocal()
        session.add(score)
        session.commit()
        session.close()

    # Test the endpoint
    client = TestClient(app)

    # Test server1 (TRUSTED_GENERAL)
    response = client.get("/api/servers/server1/verdict")
    assert response.status_code == 200
    assert response.json()["risk_tier"] == "TRUSTED_GENERAL"

    # Test server2 (TRUSTED_RESEARCH)
    response = client.get("/api/servers/server2/verdict")
    assert response.status_code == 200
    assert response.json()["risk_tier"] == "TRUSTED_RESEARCH"

    # Test server3 (ENTERPRISE_CONTROLLED)
    response = client.get("/api/servers/server3/verdict")
    assert response.status_code == 200
    assert response.json()["risk_tier"] == "ENTERPRISE_CONTROLLED"

    print("PASS")