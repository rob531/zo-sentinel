import time
import requests
from typing import Dict, Optional
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from fastapi import Depends
from sqlalchemy.orm import Session

def compute_risk_tier(server_id: str, db: Session) -> str:
    # Fetch axis scores for the server
    axis_scores = db.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id,
        MCPLLMAxisScores.axis_name.in_([
            'overall_risk', 'auth_strength', 'capability_breadth',
            'data_sensitivity', 'network_egress', 'maintainer_trust',
            'exploit_surface'
        ])
    ).order_by(MCPLLMAxisScores.scored_at.desc()).first()

    if not axis_scores:
        return "unknown"

    # Calculate risk tier based on axis scores
    p_top = axis_scores.p_top
    p_critical = axis_scores.p_critical
    p_danger = axis_scores.p_danger

    if p_danger > 0.7:
        return "high"
    elif p_critical > 0.5:
        return "medium"
    elif p_top > 0.3:
        return "low"
    else:
        return "minimal"

def update_risk_tier(server_id: str, risk_tier: str) -> None:
    payload = {
        "table": "mcp_server_registry",
        "rows": [
            {
                "server_id": server_id,
                "risk_tier": risk_tier
            }
        ],
        "wait": True
    }
    response = requests.post("http://127.0.0.1:8772/write", json=payload)
    response.raise_for_status()

def run() -> None:
    while True:
        try:
            db = next(get_session())
            servers = db.query(MCPServerRegistry).all()

            for server in servers:
                risk_tier = compute_risk_tier(server.server_id, db)
                update_risk_tier(server.server_id, risk_tier)

            # Send heartbeat
            requests.post("http://127.0.0.1:8772/service_health", json={"status": "alive"})

            time.sleep(60)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    import requests_mock

    app = FastAPI()
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Insert test data
    with TestSession() as session:
        test_server = MCPServerRegistry(server_id="test_server", risk_tier="unknown")
        session.add(test_server)
        session.commit()

        test_scores = MCPLLMAxisScores(
            server_id="test_server",
            axis_name="overall_risk",
            p_top=0.4,
            p_critical=0.6,
            p_danger=0.2,
            scored_at="2023-01-01T00:00:00Z"
        )
        session.add(test_scores)
        session.commit()

    with requests_mock.Mocker() as m:
        m.post("http://127.0.0.1:8772/write", json={"status": "success"})

        compute_risk_tier("test_server", next(override_get_session()))
        update_risk_tier("test_server", "medium")

        # Verify the POST payload
        last_request = m.last_request
        payload = last_request.json()
        assert payload["table"] == "mcp_server_registry"
        assert payload["rows"][0]["server_id"] == "test_server"
        assert payload["rows"][0]["risk_tier"] == "medium"

        print("PASS")