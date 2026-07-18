from fastapi import FastAPI, Depends, HTTPException
from typing import List, Dict
import requests
import time
from app.db import get_session
from app.models import MCPLLMAxisScores, MCPServerRegistry

app = FastAPI()

def compute_composite(server_id: str, scores: List[Dict]) -> str:
    if not scores:
        raise ValueError("No scores provided")

    axis_names = ['overall_risk', 'auth_strength', 'capability_breadth',
                  'data_sensitivity', 'network_egress', 'maintainer_trust',
                  'exploit_surface']

    if len(scores) != 7 or any(score['axis_name'] not in axis_names for score in scores):
        raise ValueError("Invalid scores format")

    p_tops = [score['p_top'] for score in scores]
    avg_score = sum(p_tops) / len(p_tops)

    if avg_score > 75:
        return 'TRUSTED_GENERAL'
    elif avg_score > 60:
        return 'TRUSTED_RESEARCH'
    elif avg_score > 45:
        return 'ENTERPRISE_CONTROLLED'
    elif avg_score > 30:
        return 'CAUTION_LIMITED'
    elif avg_score > 15:
        return 'HIGH_RISK_ISOLATED'
    else:
        return 'KNOWN_THREAT'

def sync_server(server_id: str, session=Depends(get_session)) -> bool:
    try:
        # Check if server exists in registry
        server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
        if not server:
            return False

        # Get all axis scores for the server
        scores = session.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()
        if not scores:
            return False

        # Convert to dict format expected by compute_composite
        scores_dict = [{'axis_name': score.axis_name, 'p_top': score.p_top} for score in scores]

        # Compute composite risk tier
        risk_tier = compute_composite(server_id, scores_dict)

        # Update risk tier in registry
        server.risk_tier = risk_tier
        session.commit()

        # Write to write_service
        write_service_url = "http://127.0.0.1:8772/write"
        payload = {'server_id': server_id, 'risk_tier': risk_tier}
        response = requests.post(write_service_url, json=payload)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to write to write_service")

        return True
    except Exception as e:
        print(f"Error syncing server {server_id}: {e}")
        return False

def run() -> None:
    last_heartbeat = time.time()

    while True:
        try:
            # Process all servers
            session = next(get_session())
            servers = session.query(MCPServerRegistry).all()
            for server in servers:
                sync_server(server.server_id, session)

            # Heartbeat
            current_time = time.time()
            if current_time - last_heartbeat > 60:
                heartbeat_payload = {'service': 'axis_score_aggregation_consumer', 'last_heartbeat': current_time}
                requests.post("http://127.0.0.1:8772/write", json={'table': 'service_health', 'rows': [heartbeat_payload]})
                last_heartbeat = current_time

            time.sleep(10)
        except Exception as e:
            print(f"Error in main loop: {e}")
            time.sleep(10)

if __name__ == '__main__':
    # Self-test
    from app.db import get_session
    from app.models import MCPLLMAxisScores, MCPServerRegistry
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override get_session for testing
    engine = create_engine('sqlite:///:memory:')
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test tables
    MCPServerRegistry.__table__.create(engine)
    MCPLLMAxisScores.__table__.create(engine)

    # Test case 1: TRUSTED_GENERAL
    test_server_id_1 = "test_server_1"
    test_scores_1 = [
        {'axis_name': 'overall_risk', 'p_top': 80},
        {'axis_name': 'auth_strength', 'p_top': 90},
        {'axis_name': 'capability_breadth', 'p_top': 70},
        {'axis_name': 'data_sensitivity', 'p_top': 65},
        {'axis_name': 'network_egress', 'p_top': 85},
        {'axis_name': 'maintainer_trust', 'p_top': 60},
        {'axis_name': 'exploit_surface', 'p_top': 75}
    ]

    # Test case 2: HIGH_RISK_ISOLATED
    test_server_id_2 = "test_server_2"
    test_scores_2 = [
        {'axis_name': 'overall_risk', 'p_top': 20},
        {'axis_name': 'auth_strength', 'p_top': 25},
        {'axis_name': 'capability_breadth', 'p_top': 30},
        {'axis_name': 'data_sensitivity', 'p_top': 20},
        {'axis_name': 'network_egress', 'p_top': 15},
        {'axis_name': 'maintainer_trust', 'p_top': 10},
        {'axis_name': 'exploit_surface', 'p_top': 20}
    ]

    # Add test data
    session = SessionLocal()
    session.add(MCPServerRegistry(server_id=test_server_id_1))
    session.add(MCPServerRegistry(server_id=test_server_id_2))
    for score in test_scores_1:
        session.add(MCPLLMAxisScores(server_id=test_server_id_1, **score))
    for score in test_scores_2:
        session.add(MCPLLMAxisScores(server_id=test_server_id_2, **score))
    session.commit()

    # Run tests
    tier_1 = compute_composite(test_server_id_1, test_scores_1)
    tier_2 = compute_composite(test_server_id_2, test_scores_2)

    if tier_1 == 'TRUSTED_GENERAL' and tier_2 == 'HIGH_RISK_ISOLATED':
        print("PASS")
    else:
        print("FAIL")