from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, TrustGatingOverride
from services.staged.scoring_to_risk_tier_consumer.logic import compute_risk_tier
from typing import List, Dict, Optional
import pytest

app = FastAPI()

def get_axis_scores(server_id: str, session: Session = Depends(get_session)) -> List[Dict]:
    return session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()

def get_trust_override(server_id: str, session: Session = Depends(get_session)) -> bool:
    return session.query(TrustGatingOverride).filter(TrustGatingOverride.server_id == server_id).first().trusted

@app.get("/api/risk/tier/{server_id}")
async def get_risk_tier(server_id: str, session: Session = Depends(get_session)):
    axis_scores = get_axis_scores(server_id, session)
    if not axis_scores:
        raise HTTPException(status_code=404, detail="Server not found")

    trust_override = get_trust_override(server_id, session)
    risk_tier = compute_risk_tier(server_id, axis_scores)
    criteria_version = axis_scores[0].decision_rule_version

    axes_summary = {
        axis.axis_name: {
            "p_top": axis.p_top,
            "p_critical": axis.p_critical
        }
        for axis in axis_scores
    }

    return {
        "server_id": server_id,
        "risk_tier": risk_tier,
        "criteria_version": criteria_version,
        "override_applied": trust_override,
        "axes_summary": axes_summary
    }

def test_acceptance():
    from app.db import Base, engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the dependency for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    client = TestClient(app)

    # Seed test data
    test_session = TestSession()
    test_server_ids = ["server1", "server2", "server3", "server4"]

    for i, server_id in enumerate(test_server_ids):
        # Create server registry entry
        test_session.add(McpServerRegistry(
            id=server_id,
            name=f"Test Server {i+1}",
            org_id="test_org",
            owner_id="test_user"
        ))

        # Create axis scores
        p_top_values = [80, 65, 50, 20]  # One per tier boundary
        p_critical_values = [0.0, 0.0, 0.0, 0.8]  # Last one forces HIGH_RISK_ISOLATED

        for axis_name in ["overall_risk", "auth_strength", "capability_breadth",
                         "data_sensitivity", "network_egress", "maintainer_trust",
                         "exploit_surface"]:
            test_session.add(McpLlmAxisScore(
                server_id=server_id,
                axis_name=axis_name,
                p_top=p_top_values[i],
                p_critical=p_critical_values[i],
                decision_rule_version="1.0"
            ))

        # Create trust override for one server
        if i == 0:
            test_session.add(TrustGatingOverride(
                server_id=server_id,
                trusted=True
            ))

    test_session.commit()

    # Test each server
    for i, server_id in enumerate(test_server_ids):
        response = client.get(f"/api/risk/tier/{server_id}")
        assert response.status_code == 200
        data = response.json()

        # Verify risk tier based on p_top values
        if i == 0:
            assert data["risk_tier"] == "TRUSTED_GENERAL"  # Trust override
        elif i == 1:
            assert data["risk_tier"] == "TRUSTED_RESEARCH"
        elif i == 2:
            assert data["risk_tier"] == "ENTERPRISE_CONTROLLED"
        elif i == 3:
            assert data["risk_tier"] == "HIGH_RISK_ISOLATED"  # Forced by p_critical

        assert data["criteria_version"] == "1.0"
        assert "override_applied" in data

    print("PASS")

if __name__ == "__main__":
    test_acceptance()