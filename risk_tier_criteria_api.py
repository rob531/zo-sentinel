from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, Org, User
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import requests

router = APIRouter()

class RiskTier(BaseModel):
    name: str
    label: str
    description: str
    thresholds: Dict[str, float]

class RiskAxis(BaseModel):
    name: str
    label: str

@router.get("/risk-tiers/criteria", response_model=List[RiskTier])
async def get_risk_tier_criteria():
    tiers = [
        RiskTier(
            name="TRUSTED_GENERAL",
            label="Trusted General",
            description="Lowest risk tier. Suitable for general deployment with minimal restrictions.",
            thresholds={"composite_score": 75.0}
        ),
        RiskTier(
            name="TRUSTED_ISOLATED",
            label="Trusted Isolated",
            description="Low risk but requires network isolation. Suitable for internal use with controlled egress.",
            thresholds={"composite_score": 60.0}
        ),
        RiskTier(
            name="MODERATE_GENERAL",
            label="Moderate General",
            description="Moderate risk. Requires additional monitoring and controls for general deployment.",
            thresholds={"composite_score": 45.0}
        ),
        RiskTier(
            name="MODERATE_ISOLATED",
            label="Moderate Isolated",
            description="Moderate risk but requires network isolation. Suitable for limited internal use.",
            thresholds={"composite_score": 30.0}
        ),
        RiskTier(
            name="HIGH_RISK_ISOLATED",
            label="High Risk Isolated",
            description="High risk. Must be deployed in isolated environments with strict controls.",
            thresholds={"composite_score": 15.0}
        ),
        RiskTier(
            name="HIGH_RISK_QUARANTINE",
            label="High Risk Quarantine",
            description="Highest risk tier. Must be quarantined and not connected to any production systems.",
            thresholds={"composite_score": 0.0}
        ),
        RiskTier(
            name="INSUFFICIENT",
            label="Insufficient Data",
            description="Insufficient signals to determine risk. Requires additional data collection.",
            thresholds={"missing_signals": 5.0}
        )
    ]
    return tiers

@router.get("/risk-tiers/axes", response_model=List[RiskAxis])
async def get_risk_axes():
    axes = [
        RiskAxis(name="overall_risk", label="Overall Risk"),
        RiskAxis(name="auth_strength", label="Authentication Strength"),
        RiskAxis(name="capability_breadth", label="Capability Breadth"),
        RiskAxis(name="data_sensitivity", label="Data Sensitivity"),
        RiskAxis(name="network_egress", label="Network Egress"),
        RiskAxis(name="maintainer_trust", label="Maintainer Trust"),
        RiskAxis(name="exploit_surface", label="Exploit Surface")
    ]
    return axes

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import get_session, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    client = TestClient(app)

    # Test /risk-tiers/criteria
    response = client.get("/risk-tiers/criteria")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 7
    for tier in data:
        assert "name" in tier
        assert "label" in tier
        assert "description" in tier
        assert "thresholds" in tier

    # Test /risk-tiers/axes
    response = client.get("/risk-tiers/axes")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 7
    for axis in data:
        assert "name" in axis
        assert "label" in axis

    print("PASS")