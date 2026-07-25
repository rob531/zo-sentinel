import requests
from typing import Dict, List
from app.db import get_session
from app.models import MCPLLMAxisScores, MCPServerRegistry
from fastapi import Depends
from sqlalchemy.orm import Session

def compute_risk_tier(axis_scores: Dict[str, float]) -> str:
    overall_risk = axis_scores.get('overall_risk', 0.0)
    auth_strength = axis_scores.get('auth_strength', 0.0)
    capability_breadth = axis_scores.get('capability_breadth', 0.0)
    data_sensitivity = axis_scores.get('data_sensitivity', 0.0)
    network_egress = axis_scores.get('network_egress', 0.0)
    maintainer_trust = axis_scores.get('maintainer_trust', 0.0)
    exploit_surface = axis_scores.get('exploit_surface', 0.0)

    if overall_risk >= 0.8:
        return 'HIGH_RISK_ISOLATED'
    elif overall_risk >= 0.6:
        return 'HIGH_RISK'
    elif overall_risk >= 0.4:
        return 'MEDIUM_RISK'
    elif overall_risk >= 0.2:
        return 'LOW_RISK'
    else:
        return 'MINIMAL_RISK'

def get_axis_scores_for_server(db: Session, server_id: str) -> Dict[str, float]:
    scores = db.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id,
        MCPLLMAxisScores.axis_name.in_([
            'overall_risk', 'auth_strength', 'capability_breadth',
            'data_sensitivity', 'network_egress', 'maintainer_trust',
            'exploit_surface'
        ])
    ).all()

    return {score.axis_name: score.p_top for score in scores}

def update_server_risk_tier(db: Session, server_id: str, risk_tier: str) -> None:
    server = db.query(MCPServerRegistry).filter(
        MCPServerRegistry.server_id == server_id
    ).first()

    if server and server.risk_tier != risk_tier:
        server.risk_tier = risk_tier
        db.commit()

def write_to_write_service(server_id: str, risk_tier: str) -> None:
    payload = {
        'table': 'mcp_server_registry',
        'rows': {
            'server_id': server_id,
            'risk_tier': risk_tier
        },
        'wait': True
    }
    requests.post('http://127.0.0.1:8772/write', json=payload)

def process_server(db: Session, server_id: str) -> None:
    axis_scores = get_axis_scores_for_server(db, server_id)
    if not axis_scores:
        return

    risk_tier = compute_risk_tier(axis_scores)
    update_server_risk_tier(db, server_id, risk_tier)
    write_to_write_service(server_id, risk_tier)

def run() -> None:
    db = Depends(get_session)()
    servers = db.query(MCPServerRegistry.server_id).all()
    for server in servers:
        process_server(db, server.server_id)
    db.close()

if __name__ == '__main__':
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import requests_mock
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    test_db = create_engine('sqlite:///:memory:')
    TestSession = sessionmaker(bind=test_db)
    app.dependency_overrides[get_session] = lambda: TestSession()

    from app.models import Base
    Base.metadata.create_all(test_db)

    # Insert test data
    test_session = TestSession()
    test_server = MCPServerRegistry(server_id='srv-123', risk_tier='UNKNOWN')
    test_session.add(test_server)
    test_axis_scores = [
        MCPLLMAxisScores(server_id='srv-123', axis_name='overall_risk', p_top=0.85),
        MCPLLMAxisScores(server_id='srv-123', axis_name='auth_strength', p_top=0.7),
        MCPLLMAxisScores(server_id='srv-123', axis_name='capability_breadth', p_top=0.6),
        MCPLLMAxisScores(server_id='srv-123', axis_name='data_sensitivity', p_top=0.9),
        MCPLLMAxisScores(server_id='srv-123', axis_name='network_egress', p_top=0.5),
        MCPLLMAxisScores(server_id='srv-123', axis_name='maintainer_trust', p_top=0.4),
        MCPLLMAxisScores(server_id='srv-123', axis_name='exploit_surface', p_top=0.8)
    ]
    test_session.add_all(test_axis_scores)
    test_session.commit()

    with requests_mock.Mocker() as m:
        m.post('http://127.0.0.1:8772/write', json={'status': 'success'})

        run()

        # Verify the POST was made with the correct data
        assert m.last_request.json()['rows']['risk_tier'] == 'HIGH_RISK_ISOLATED'
        print('PASS')