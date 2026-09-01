from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import and_

class ServerRegistryResponse(BaseModel):
    id: int
    org_id: int
    hostname: str
    ip_address: str
    status: str
    last_heartbeat: Optional[str] = None

class AxisScoreResponse(BaseModel):
    id: int
    server_id: int
    axis: str
    score: float
    timestamp: str

class ScoreDisputeResponse(BaseModel):
    id: int
    score_id: int
    dispute_reason: str
    status: str
    created_at: str

class OrgResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None

class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    org_id: int

class SentinelService:
    def __init__(self):
        self.db = get_session()

    def get_server_by_id(self, server_id: int) -> Optional[ServerRegistryResponse]:
        server = self.db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
        if server:
            return ServerRegistryResponse(
                id=server.id,
                org_id=server.org_id,
                hostname=server.hostname,
                ip_address=server.ip_address,
                status=server.status,
                last_heartbeat=server.last_heartbeat.isoformat() if server.last_heartbeat else None
            )
        return None

    def get_scores_by_server(self, server_id: int) -> List[AxisScoreResponse]:
        scores = self.db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()
        return [
            AxisScoreResponse(
                id=score.id,
                server_id=score.server_id,
                axis=score.axis,
                score=score.score,
                timestamp=score.timestamp.isoformat()
            ) for score in scores
        ]

    def get_disputes_by_score(self, score_id: int) -> List[ScoreDisputeResponse]:
        disputes = self.db.query(McpScoreDispute).filter(McpScoreDispute.score_id == score_id).all()
        return [
            ScoreDisputeResponse(
                id=dispute.id,
                score_id=dispute.score_id,
                dispute_reason=dispute.dispute_reason,
                status=dispute.status,
                created_at=dispute.created_at.isoformat()
            ) for dispute in disputes
        ]

    def get_org_by_id(self, org_id: int) -> Optional[OrgResponse]:
        org = self.db.query(Org).filter(Org.id == org_id).first()
        if org:
            return OrgResponse(
                id=org.id,
                name=org.name,
                description=org.description
            )
        return None

    def get_users_by_org(self, org_id: int) -> List[UserResponse]:
        users = self.db.query(User).filter(User.org_id == org_id).all()
        return [
            UserResponse(
                id=user.id,
                username=user.username,
                email=user.email,
                org_id=user.org_id
            ) for user in users
        ]

def get_sentinel_service() -> SentinelService:
    return SentinelService()

if __name__ == "__main__":
    # Self-test
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Override dependencies for testing
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: Session(test_engine)

    # Test data
    test_org = Org(name="Test Org", description="Test Description")
    test_user = User(username="testuser", email="test@example.com", org_id=1)
    test_server = McpServerRegistry(
        org_id=1,
        hostname="test.example.com",
        ip_address="192.168.1.1",
        status="active"
    )
    test_score = McpLlmAxisScore(
        server_id=1,
        axis="test_axis",
        score=0.95
    )
    test_dispute = McpScoreDispute(
        score_id=1,
        dispute_reason="Test dispute",
        status="open"
    )

    # Add test data
    with Session(test_engine) as session:
        session.add(test_org)
        session.add(test_user)
        session.add(test_server)
        session.add(test_score)
        session.add(test_dispute)
        session.commit()

    # Test service
    service = get_sentinel_service()

    # Test get_server_by_id
    server = service.get_server_by_id(1)
    assert server is not None
    assert server.hostname == "test.example.com"

    # Test get_scores_by_server
    scores = service.get_scores_by_server(1)
    assert len(scores) == 1
    assert scores[0].axis == "test_axis"

    # Test get_disputes_by_score
    disputes = service.get_disputes_by_score(1)
    assert len(disputes) == 1
    assert disputes[0].dispute_reason == "Test dispute"

    # Test get_org_by_id
    org = service.get_org_by_id(1)
    assert org is not None
    assert org.name == "Test Org"

    # Test get_users_by_org
    users = service.get_users_by_org(1)
    assert len(users) == 1
    assert users[0].username == "testuser"

    print("PASS")