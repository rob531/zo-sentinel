from typing import Dict, List
from app.db import get_session
from app.models import MCPLLMAxisScores
from fastapi import Depends
import requests
from unittest.mock import patch, MagicMock

def compute_risk_tier(server_id: str) -> Dict:
    session = Depends(get_session)
    axis_scores = session.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()

    axes = {}
    critical_threshold_exceeded = False

    for score in axis_scores:
        axes[score.axis_name] = score.p_top
        if score.p_critical > 0.8:
            critical_threshold_exceeded = True

    if critical_threshold_exceeded:
        return {
            'overall_risk': 0.0,
            'risk_tier': 'HIGH_RISK_ISOLATED',
            'axes': axes
        }

    if not axes:
        return {
            'overall_risk': 0.0,
            'risk_tier': 'INSUFFICIENT',
            'axes': axes
        }

    overall_risk = sum(axes.values()) / len(axes) * 100

    if overall_risk >= 75:
        risk_tier = 'TRUSTED_GENERAL'
    elif overall_risk >= 60:
        risk_tier = 'TRUSTED_RESEARCH'
    elif overall_risk >= 45:
        risk_tier = 'ENTERPRISE_CONTROLLED'
    elif overall_risk >= 30:
        risk_tier = 'CAUTION_LIMITED'
    elif overall_risk >= 15:
        risk_tier = 'HIGH_RISK_ISOLATED'
    else:
        risk_tier = 'INSUFFICIENT'

    return {
        'overall_risk': overall_risk,
        'risk_tier': risk_tier,
        'axes': axes
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    test_server_id = "test_server_123"
    test_axis_scores = [
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="axis1",
            p_top=0.8,
            p_critical=0.7,
            p_danger=0.1
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="axis2",
            p_top=0.7,
            p_critical=0.75,
            p_danger=0.15
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="axis3",
            p_top=0.9,
            p_critical=0.85,
            p_danger=0.2
        )
    ]

    with TestSessionLocal() as session:
        session.add_all(test_axis_scores)
        session.commit()

    result = compute_risk_tier(test_server_id)

    expected_keys = {'overall_risk', 'risk_tier', 'axes'}
    assert set(result.keys()) == expected_keys, "Missing or extra keys in result"

    expected_axes = {score.axis_name: score.p_top for score in test_axis_scores}
    assert result['axes'] == expected_axes, "Axes do not match"

    expected_overall_risk = sum(score.p_top for score in test_axis_scores) / len(test_axis_scores) * 100
    assert result['overall_risk'] == expected_overall_risk, "Overall risk does not match"

    if any(score.p_critical > 0.8 for score in test_axis_scores):
        assert result['risk_tier'] == 'HIGH_RISK_ISOLATED', "Risk tier should be HIGH_RISK_ISOLATED"
    else:
        if expected_overall_risk >= 75:
            assert result['risk_tier'] == 'TRUSTED_GENERAL', "Risk tier does not match"
        elif expected_overall_risk >= 60:
            assert result['risk_tier'] == 'TRUSTED_RESEARCH', "Risk tier does not match"
        elif expected_overall_risk >= 45:
            assert result['risk_tier'] == 'ENTERPRISE_CONTROLLED', "Risk tier does not match"
        elif expected_overall_risk >= 30:
            assert result['risk_tier'] == 'CAUTION_LIMITED', "Risk tier does not match"
        elif expected_overall_risk >= 15:
            assert result['risk_tier'] == 'HIGH_RISK_ISOLATED', "Risk tier does not match"
        else:
            assert result['risk_tier'] == 'INSUFFICIENT', "Risk tier does not match"

    print("PASS")