from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry
from sqlalchemy.orm import Session

class Dispute(BaseModel):
    id: str
    submitted_by: str
    proposed_overall_risk: float
    proposed_axes: dict
    reason_category: str
    status: str
    created_at: datetime
    resolved_at: Optional[datetime]

class DisputeResponse(BaseModel):
    server_id: str
    server_name: str
    disputes: List[Dispute]

def get_dispute(server_id: str, session: Session = Depends(get_session)) -> DisputeResponse:
    server = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    disputes = session.query(McpScoreDispute).filter(McpScoreDispute.server_id == server_id).all()

    response = DisputeResponse(
        server_id=server.server_id,
        server_name=server.name,
        disputes=[
            Dispute(
                id=str(dispute.id),
                submitted_by=dispute.submitted_by,
                proposed_overall_risk=dispute.proposed_overall_risk,
                proposed_axes=dispute.proposed_axes,
                reason_category=dispute.reason_category,
                status=dispute.status,
                created_at=dispute.created_at,
                resolved_at=dispute.resolved_at
            ) for dispute in disputes
        ]
    )

    return response

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    session = SessionLocal()
    test_server = McpServerRegistry(
        server_id="test123",
        name="Test Server",
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
    session.add(test_server)
    session.commit()

    dispute1 = McpScoreDispute(
        server_id="test123",
        submitted_by="user1",
        proposed_overall_risk=0.8,
        proposed_axes={"axis1": 0.7, "axis2": 0.9},
        reason_category="technical",
        status="open",
        created_at=datetime.now(),
        resolved_at=None
    )
    dispute2 = McpScoreDispute(
        server_id="test123",
        submitted_by="user2",
        proposed_overall_risk=0.6,
        proposed_axes={"axis1": 0.5, "axis2": 0.7},
        reason_category="policy",
        status="resolved",
        created_at=datetime.now(),
        resolved_at=datetime.now()
    )
    session.add_all([dispute1, dispute2])
    session.commit()

    # Create FastAPI app and test client
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/api/dispute/test123")
    assert response.status_code == 200
    assert len(response.json()["disputes"]) == 2
    assert response.json()["server_id"] == "test123"
    assert response.json()["server_name"] == "Test Server"

    print("PASS")