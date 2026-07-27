from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

router = APIRouter(prefix="/api")

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

@router.get("/dispute/{server_id}", response_model=DisputeResponse)
def get_dispute(server_id: str, session: Session = Depends(get_session)) -> DisputeResponse:
    server = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    disputes = session.query(McpScoreDispute).filter(McpScoreDispute.server_id == server_id).all()

    return DisputeResponse(
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

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    app.dependency_overrides[get_session] = lambda: test_session

    # Insert test data
    test_server = McpServerRegistry(server_id="test123", name="Test Server")
    test_session.add(test_server)
    test_session.commit()

    test_dispute1 = McpScoreDispute(
        server_id="test123",
        submitted_by="user1",
        proposed_overall_risk=0.8,
        proposed_axes={"axis1": 0.7, "axis2": 0.9},
        reason_category="category1",
        status="pending",
        created_at=datetime.now()
    )
    test_dispute2 = McpScoreDispute(
        server_id="test123",
        submitted_by="user2",
        proposed_overall_risk=0.6,
        proposed_axes={"axis1": 0.5, "axis2": 0.7},
        reason_category="category2",
        status="resolved",
        created_at=datetime.now(),
        resolved_at=datetime.now()
    )
    test_session.add_all([test_dispute1, test_dispute2])
    test_session.commit()

    client = TestClient(app)
    response = client.get("/api/dispute/test123")
    assert response.status_code == 200
    assert len(response.json()["disputes"]) == 2
    print("PASS")