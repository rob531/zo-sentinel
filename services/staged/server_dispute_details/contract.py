from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api")

class Dispute(BaseModel):
    id: int
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

@router.get("/dispute/{server_id}", response_model=DisputeResponse)
async def get_dispute(server_id: str, db: Session = Depends(get_session)) -> DisputeResponse:
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    disputes = db.query(McpScoreDispute).filter(McpScoreDispute.server_id == server_id).all()

    return DisputeResponse(
        server_id=server.server_id,
        server_name=server.name,
        disputes=[
            Dispute(
                id=dispute.id,
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

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    test_server = McpServerRegistry(server_id="test123", name="Test Server")
    test_dispute1 = McpScoreDispute(
        server_id="test123",
        submitted_by="user1",
        proposed_overall_risk=0.5,
        proposed_axes={"axis1": 0.3, "axis2": 0.7},
        reason_category="test",
        status="pending",
        created_at=datetime.now(),
        resolved_at=None
    )
    test_dispute2 = McpScoreDispute(
        server_id="test123",
        submitted_by="user2",
        proposed_overall_risk=0.8,
        proposed_axes={"axis1": 0.4, "axis2": 0.6},
        reason_category="test",
        status="resolved",
        created_at=datetime.now(),
        resolved_at=datetime.now()
    )

    session = SessionLocal()
    session.add(test_server)
    session.add(test_dispute1)
    session.add(test_dispute2)
    session.commit()

    # Create FastAPI app and test client
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/api/dispute/test123")
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == "test123"
    assert data["server_name"] == "Test Server"
    assert len(data["disputes"]) == 2
    assert data["disputes"][0]["submitted_by"] == "user1"
    assert data["disputes"][1]["submitted_by"] == "user2"

    print("PASS")