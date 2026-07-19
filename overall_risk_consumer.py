import time
import json
from typing import List, Dict, Optional
from fastapi import Depends
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
import requests
from sqlalchemy.orm import Session

def calculate_risk_tier(axis_scores: List[Dict]) -> str:
    critical_axes = [score for score in axis_scores if score['p_critical'] > 0.8]
    if critical_axes:
        return 'HIGH_RISK_ISOLATED'

    p_top_values = [score['p_top'] for score in axis_scores]
    avg_p_top = sum(p_top_values) / len(p_top_values)

    if avg_p_top > 0.7:
        return 'TRUSTED_GENERAL'
    elif avg_p_top > 0.5:
        return 'TRUSTED_RESEARCH'
    else:
        return 'CAUTION_LIMITED'

def get_axis_scores(db: Session) -> List[Dict]:
    results = db.query(
        MCPLLMAxisScores.server_id,
        MCPLLMAxisScores.axis_name,
        MCPLLMAxisScores.p_top,
        MCPLLMAxisScores.p_critical
    ).all()

    scores_by_server = {}
    for row in results:
        if row.server_id not in scores_by_server:
            scores_by_server[row.server_id] = []
        scores_by_server[row.server_id].append({
            'axis_name': row.axis_name,
            'p_top': row.p_top,
            'p_critical': row.p_critical
        })

    return scores_by_server

def update_server_risk(db: Session, server_id: int, overall_risk: float, risk_tier: str) -> None:
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if server:
        server.overall_risk = overall_risk
        server.risk_tier = risk_tier
        db.commit()

def send_heartbeat() -> None:
    try:
        requests.post('http://127.0.0.1:8772/service_health', json={'status': 'alive'})
    except requests.RequestException:
        pass

def run() -> None:
    db = Depends(get_session)()
    last_heartbeat = time.time()

    while True:
        try:
            scores_by_server = get_axis_scores(db)

            for server_id, axis_scores in scores_by_server.items():
                risk_tier = calculate_risk_tier(axis_scores)
                overall_risk = sum(score['p_top'] for score in axis_scores) / len(axis_scores)

                update_server_risk(db, server_id, overall_risk, risk_tier)

                try:
                    response = requests.post(
                        'http://127.0.0.1:8772/write',
                        json={
                            'table': 'mcp_server_registry',
                            'rows': {
                                'server_id': server_id,
                                'overall_risk': overall_risk,
                                'risk_tier': risk_tier
                            },
                            'wait': True
                        }
                    )
                    response.raise_for_status()
                except requests.RequestException:
                    pass

            current_time = time.time()
            if current_time - last_heartbeat > 60:
                send_heartbeat()
                last_heartbeat = current_time

            time.sleep(10)

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(30)

if __name__ == '__main__':
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from unittest.mock import patch

    test_engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_session = TestSession()

    test_server1 = MCPServerRegistry(server_id=1, name='Test Server 1')
    test_server2 = MCPServerRegistry(server_id=2, name='Test Server 2')
    test_session.add_all([test_server1, test_server2])
    test_session.commit()

    test_axis_scores = [
        MCPLLMAxisScores(server_id=1, axis_name='axis1', p_top=0.6, p_critical=0.7),
        MCPLLMAxisScores(server_id=1, axis_name='axis2', p_top=0.8, p_critical=0.1),
        MCPLLMAxisScores(server_id=2, axis_name='axis1', p_top=0.9, p_critical=0.2),
        MCPLLMAxisScores(server_id=2, axis_name='axis2', p_top=0.3, p_critical=0.85)
    ]
    test_session.add_all(test_axis_scores)
    test_session.commit()

    mock_responses = []

    def mock_post(url, json=None):
        if url == 'http://127.0.0.1:8772/write':
            mock_responses.append(json)
            return requests.Response()
        return requests.Response()

    with patch('requests.post', side_effect=mock_post):
        app.dependency_overrides[get_session] = lambda: test_session
        run()

        expected_tiers = {
            1: 'TRUSTED_RESEARCH',
            2: 'HIGH_RISK_ISOLATED'
        }

        for response in mock_responses:
            server_id = response['rows']['server_id']
            tier = response['rows']['risk_tier']
            if tier != expected_tiers[server_id]:
                print('FAIL')
                break
        else:
            print('PASS')