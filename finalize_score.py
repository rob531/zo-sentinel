from typing import Dict, List
from fastapi import Depends
from app.db import get_session
from app.models import MCPLLMAxisScore
import requests

def compute_final_risk(server_id: str) -> Dict:
    session = Depends(get_session)
    axis_scores = session.query(MCPLLMAxisScore).filter(MCPLLMAxisScore.server_id == server_id).all()

    if not axis_scores:
        return {
            'overall_risk': 0.0,
            'risk_tier': 'UNKNOWN',
            'details': []
        }

    total_p_top = 0.0
    max_p_critical = 0.0
    max_p_danger = 0.0
    details = []

    for score in axis_scores:
        axis_detail = {
            'axis_name': score.axis_name,
            'p_top': score.p_top,
            'p_critical': score.p_critical,
            'p_danger': score.p_danger
        }
        details.append(axis_detail)

        total_p_top += score.p_top
        if score.p_critical > max_p_critical:
            max_p_critical = score.p_critical
        if score.p_danger > max_p_danger:
            max_p_danger = score.p_danger

    overall_risk = total_p_top / len(axis_scores)

    if max_p_danger > 0.7:
        risk_tier = 'CRITICAL'
    elif max_p_critical > 0.5:
        risk_tier = 'HIGH'
    elif overall_risk > 0.3:
        risk_tier = 'MEDIUM'
    else:
        risk_tier = 'LOW'

    return {
        'overall_risk': overall_risk,
        'risk_tier': risk_tier,
        'details': details
    }

if __name__ == '__main__':
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base, MCPLLMAxisScore
    from fastapi.testclient import TestClient
    from app.main import app

    # Setup in-memory SQLite for testing
    test_engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Insert test data
    test_session = TestSession()
    test_server_id = "test_server_123"
    test_axis_scores = [
        MCPLLMAxisScore(
            server_id=test_server_id,
            axis_name="axis1",
            p_top=0.8,
            p_critical=0.6,
            p_danger=0.2
        ),
        MCPLLMAxisScore(
            server_id=test_server_id,
            axis_name="axis2",
            p_top=0.6,
            p_critical=0.4,
            p_danger=0.1
        ),
        MCPLLMAxisScore(
            server_id=test_server_id,
            axis_name="axis3",
            p_top=0.4,
            p_critical=0.3,
            p_danger=0.0
        )
    ]
    test_session.add_all(test_axis_scores)
    test_session.commit()

    # Test the function
    result = compute_final_risk(test_server_id)

    # Verify the results
    expected_overall_risk = (0.8 + 0.6 + 0.4) / 3
    assert abs(result['overall_risk'] - expected_overall_risk) < 0.0001
    assert result['risk_tier'] == 'HIGH'
    assert len(result['details']) == 3

    print("PASS")