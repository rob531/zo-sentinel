from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

class VerdictEvent(BaseModel):
    scored_at: str
    axis_name: str
    p_top: float
    p_critical: float
    risk_tier: Optional[str]

class ServerVerdictTimeline(BaseModel):
    server_id: str
    verdict_events: List[VerdictEvent]

def get_server_verdict_timeline(server_id: str, session: Session = Depends(get_session)) -> ServerVerdictTimeline:
    # Query the database for the server's timeline
    scores = (
        session.query(
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpServerRegistry.risk_tier
        )
        .join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id
        )
        .filter(
            McpLlmAxisScore.server_id == server_id
        )
        .order_by(
            McpLlmAxisScore.scored_at.asc()
        )
        .all()
    )

    if not scores:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found or no verdict events"
        )

    # Convert the results to the expected format
    verdict_events = [
        VerdictEvent(
            scored_at=str(score.scored_at),
            axis_name=score.axis_name,
            p_top=score.p_top,
            p_critical=score.p_critical,
            risk_tier=score.risk_tier
        )
        for score in scores
    ]

    return ServerVerdictTimeline(
        server_id=server_id,
        verdict_events=verdict_events
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(engine)

    # Create a test session
    TestSession = Session.bind(engine)

    # Create test data
    test_server_id = "test-server-1"
    test_server = McpServerRegistry(
        server_id=test_server_id,
        name="Test Server",
        url="http://test.example.com",
        risk_tier="high",
        last_seen="2023-01-01T00:00:00Z",
        last_assessed="2023-01-01T00:00:00Z",
        scan_count=1,
        confidence=0.9,
        trust_score=0.8,
        verdict="malicious",
        verdict_reasoning="Test reasoning",
        registry_source="test",
        first_seen="2023-01-01T00:00:00Z",
        last_scanned="2023-01-01T00:00:00Z",
        meta={},
        description="Test server"
    )
    TestSession.add(test_server)

    test_scores = [
        McpLlmAxisScore(
            server_id=test_server_id,
            axis_name="axis1",
            p_top=0.9,
            p_critical=0.8,
            p_danger=0.7,
            probs=[0.9, 0.8, 0.7],
            label="malicious",
            label_index=0,
            model_version="1.0",
            decision_rule_version="1.0",
            adapter_sha256="test-sha",
            scored_at="2023-01-01T00:00:00Z",
            escalated=False,
            escalated_to=None
        ),
        McpLlmAxisScore(
            server_id=test_server_id,
            axis_name="axis2",
            p_top=0.8,
            p_critical=0.7,
            p_danger=0.6,
            probs=[0.8, 0.7, 0.6],
            label="malicious",
            label_index=0,
            model_version="1.0",
            decision_rule_version="1.0",
            adapter_sha256="test-sha",
            scored_at="2023-01-02T00:00:00Z",
            escalated=False,
            escalated_to=None
        ),
        McpLlmAxisScore(
            server_id=test_server_id,
            axis_name="axis3",
            p_top=0.7,
            p_critical=0.6,
            p_danger=0.5,
            probs=[0.7, 0.6, 0.5],
            label="malicious",
            label_index=0,
            model_version="1.0",
            decision_rule_version="1.0",
            adapter_sha256="test-sha",
            scored_at="2023-01-03T00:00:00Z",
            escalated=False,
            escalated_to=None
        )
    ]
    TestSession.add_all(test_scores)
    TestSession.commit()

    # Create a FastAPI app for testing
    app = FastAPI()

    # Override the get_session dependency for testing
    def get_test_session():
        return TestSession

    app.dependency_overrides[get_session] = get_test_session

    # Include the router
    from . import router
    app.include_router(router.router, prefix="/api")

    # Test the endpoint
    client = TestClient(app)
    response = client.get(f"/api/servers/{test_server_id}/verdict/timeline")

    assert response.status_code == 200
    assert response.json()["verdict_events"]

    print("PASS")