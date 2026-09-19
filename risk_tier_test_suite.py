import pytest
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, Org, User
from app.schemas import RiskTier
from app.risk_tier import calculate_risk_tier
from app.dependency_overrides import override_get_session_for_testing

def test_critical_axis_override():
    with override_get_session_for_testing():
        # Setup test data
        session = Depends(get_session)
        org = Org(name="Test Org", description="Test Org Description")
        session.add(org)
        session.commit()

        # Add axis scores with one critical axis
        critical_score = McpLlmAxisScore(
            org_id=org.id,
            axis="critical_axis",
            score=0.9,
            adapter_sha256="test_adapter_sha256"
        )
        session.add(critical_score)
        session.commit()

        # Calculate risk tier
        tier = calculate_risk_tier(org_id=org.id)

        # Verify critical axis override
        assert tier == RiskTier.CRITICAL

def test_no_axis_scores_returns_default():
    with override_get_session_for_testing():
        # Setup test data
        session = Depends(get_session)
        org = Org(name="Test Org", description="Test Org Description")
        session.add(org)
        session.commit()

        # Calculate risk tier with no axis scores
        tier = calculate_risk_tier(org_id=org.id)

        # Verify default tier
        assert tier == RiskTier.LOW

def test_boundary_conditions():
    with override_get_session_for_testing():
        # Setup test data
        session = Depends(get_session)
        org = Org(name="Test Org", description="Test Org Description")
        session.add(org)
        session.commit()

        # Add axis scores at boundary conditions
        low_score = McpLlmAxisScore(
            org_id=org.id,
            axis="low_axis",
            score=0.1,
            adapter_sha256="test_adapter_sha256"
        )
        medium_score = McpLlmAxisScore(
            org_id=org.id,
            axis="medium_axis",
            score=0.5,
            adapter_sha256="test_adapter_sha256"
        )
        high_score = McpLlmAxisScore(
            org_id=org.id,
            axis="high_axis",
            score=0.9,
            adapter_sha256="test_adapter_sha256"
        )
        session.add_all([low_score, medium_score, high_score])
        session.commit()

        # Calculate risk tier
        tier = calculate_risk_tier(org_id=org.id)

        # Verify boundary conditions
        assert tier == RiskTier.HIGH

def test_multiple_orgs_isolation():
    with override_get_session_for_testing():
        # Setup test data
        session = Depends(get_session)
        org1 = Org(name="Test Org 1", description="Test Org 1 Description")
        org2 = Org(name="Test Org 2", description="Test Org 2 Description")
        session.add_all([org1, org2])
        session.commit()

        # Add axis scores for org1
        org1_score = McpLlmAxisScore(
            org_id=org1.id,
            axis="org1_axis",
            score=0.8,
            adapter_sha256="test_adapter_sha256"
        )
        session.add(org1_score)
        session.commit()

        # Add axis scores for org2
        org2_score = McpLlmAxisScore(
            org_id=org2.id,
            axis="org2_axis",
            score=0.2,
            adapter_sha256="test_adapter_sha256"
        )
        session.add(org2_score)
        session.commit()

        # Calculate risk tiers
        tier1 = calculate_risk_tier(org_id=org1.id)
        tier2 = calculate_risk_tier(org_id=org2.id)

        # Verify isolation
        assert tier1 == RiskTier.MEDIUM
        assert tier2 == RiskTier.LOW

if __name__ == "__main__":
    import sys
    from app.dependency_overrides import override_get_session_for_testing

    with override_get_session_for_testing():
        pytest.main([__file__, "-v"])
        print("PASS")