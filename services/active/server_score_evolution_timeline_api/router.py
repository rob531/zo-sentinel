# deps: fastapi pydantic sqlalchemy sqlmodel passlib pyjwt

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, Org, User, ApiKey

router = APIRouter()

class ServerScoreTimelineResponse(BaseModel):
    server_id: str
    axis_name: str
    label: str
    label_index: int
    probs: dict
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    escalated_to: Optional[str]
    decision_rule_version: str
    model_version: str
    adapter_sha256: str
    scored_at: datetime

@router.get("/servers/{server_id}/score-timeline", response_model=List[ServerScoreTimelineResponse])
async def get_server_score_timeline(
    server_id: str,
    org_id: str = Depends(get_org_id),
    db: Session = Depends(get_session)
):
    """Get the score evolution timeline for a specific server."""
    # Query the McpLlmAxisScore table for the server's score history
    scores = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.org_id == org_id
    ).order_by(McpLlmAxisScore.scored_at.desc()).all()

    if not scores:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No score history found for this server"
        )

    return scores

def get_org_id(user: User = Depends(get_current_user)) -> str:
    """Dependency to get the org_id from the authenticated user."""
    return user.org_id

def get_current_user(
    db: Session = Depends(get_session),
    api_key: str = Depends(get_api_key)
) -> User:
    """Dependency to get the current user from the API key."""
    user = db.query(User).filter(User.api_key == api_key).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
    return user

def get_api_key(api_key: str = Depends(get_api_key_header)) -> str:
    """Dependency to get the API key from the request header."""
    return api_key

def get_api_key_header(api_key: str = Header(...)) -> str:
    """Dependency to get the API key from the Authorization header."""
    return api_key

if __name__ == "__main__":
    import pytest
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Create a test database
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    # Override the get_session dependency
    def override_get_session():
        try:
            db = test_engine.connect()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create the test client
    client = TestClient(app)

    # Test data
    test_org = Org(id="test-org", name="Test Org")
    test_user = User(id="test-user", org_id=test_org.id, username="testuser")
    test_api_key = ApiKey(id="test-key", user_id=test_user.id, key="test-key")
    test_server = McpServerRegistry(id="test-server", org_id=test_org.id, name="Test Server")
    test_score = McpLlmAxisScore(
        server_id=test_server.id,
        org_id=test_org.id,
        axis_name="overall_risk",
        label="HIGH",
        label_index=2,
        probs={"LOW": 0.1, "MEDIUM": 0.3, "HIGH": 0.6},
        p_top=0.6,
        p_critical=0.2,
        p_danger=0.1,
        escalated=False,
        escalated_to=None,
        decision_rule_version="1.0",
        model_version="1.0",
        adapter_sha256="test-hash",
        scored_at=datetime.utcnow()
    )

    # Add test data to the database
    with test_engine.connect() as db:
        db.add(test_org)
        db.add(test_user)
        db.add(test_api_key)
        db.add(test_server)
        db.add(test_score)
        db.commit()

    # Test the endpoint
    response = client.get(
        "/servers/test-server/score-timeline",
        headers={"Authorization": "Bearer test-key"}
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["server_id"] == "test-server"
    assert response.json()[0]["axis_name"] == "overall_risk"
    assert response.json()[0]["label"] == "HIGH"

    print("PASS")