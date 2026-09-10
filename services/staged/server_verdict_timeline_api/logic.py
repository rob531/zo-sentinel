from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from typing import List, Dict, Optional
from datetime import datetime
from pydantic import BaseModel

class VerdictEvent(BaseModel):
    scored_at: datetime
    axis_name: str
    p_top: float
    p_critical: float
    risk_tier: Optional[str]

class ServerVerdictTimeline(BaseModel):
    server_id: str
    verdict_events: List[VerdictEvent]

def get_server_verdict_timeline(server_id: str, session: Session = Depends(get_session)) -> ServerVerdictTimeline:
    # Query the database for the server's axis scores
    axis_scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).order_by(McpLlmAxisScore.scored_at).all()

    # Get the server's current risk tier
    server = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    risk_tier = server.risk_tier if server else None

    # Convert the axis scores to the required format
    verdict_events = [
        VerdictEvent(
            scored_at=score.scored_at,
            axis_name=score.axis_name,
            p_top=score.p_top,
            p_critical=score.p_critical,
            risk_tier=risk_tier
        )
        for score in axis_scores
    ]

    return ServerVerdictTimeline(
        server_id=server_id,
        verdict_events=verdict_events
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Set up the test database
    test_engine = create_engine("sqlite:///:memory:", echo=True)
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override the dependency to use the test database
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed the test database
    test_session = TestSession()
    test_session.add_all([
        McpServerRegistry(
            server_id="server1",
            name="Test Server 1",
            risk_tier="high",
            last_seen=datetime.now(),
            last_assessed=datetime.now(),
            scan_count=1,
            confidence=0.9,
            verdict="malicious",
            verdict_reasoning="Test reasoning"
        ),
        McpServerRegistry(
            server_id="server2",
            name="Test Server 2",
            risk_tier="medium",
            last_seen=datetime.now(),
            last_assessed=datetime.now(),
            scan_count=1,
            confidence=0.8,
            verdict="suspicious",
            verdict_reasoning="Test reasoning"
        ),
        McpLlmAxisScore(
            server_id="server1",
            axis_name="axis1",
            p_top=0.9,
            p_critical=0.8,
            scored_at=datetime.now(),
            model_version="1.0",
            decision_rule_version="1.0",
            label="malicious",
            label_index=1,
            probs=[0.1, 0.9],
            adapter_sha256="test_sha",
            escalated=False
        ),
        McpLlmAxisScore(
            server_id="server1",
            axis_name="axis2",
            p_top=0.8,
            p_critical=0.7,
            scored_at=datetime.now(),
            model_version="1.0",
            decision_rule_version="1.0",
            label="suspicious",
            label_index=2,
            probs=[0.2, 0.8],
            adapter_sha256="test_sha",
            escalated=False
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="axis1",
            p_top=0.7,
            p_critical=0.6,
            scored_at=datetime.now(),
            model_version="1.0",
            decision_rule_version="1.0",
            label="suspicious",
            label_index=2,
            probs=[0.3, 0.7],
            adapter_sha256="test_sha",
            escalated=False
        )
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(test_app)

    response = client.get("/api/servers/server1/verdict/timeline")
    assert response.status_code == 200
    assert response.json()["verdict_events"] != []

    print("PASS")