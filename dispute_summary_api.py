from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPScoreDispute
from datetime import datetime

router = APIRouter()

class DisputeResponse(BaseModel):
    id: int
    server_id: int
    submitted_by: int
    proposed_overall_risk: float
    proposed_axes: dict
    reason_category: str
    explanation: str
    status: str
    admin_note: Optional[str]
    created_at: datetime
    resolved_at: Optional[datetime]

class DisputeFilter(BaseModel):
    status: Optional[str] = None

@router.get("/servers/{server_id}/disputes", response_model=List[DisputeResponse])
async def get_server_disputes(server_id: int, db: Session = Depends(get_session)):
    disputes = db.query(MCPScoreDispute).filter(MCPScoreDispute.server_id == server_id).all()
    if not disputes:
        raise HTTPException(status_code=404, detail="No disputes found for this server")
    return disputes

@router.get("/disputes/backlog", response_model=List[DisputeResponse])
async def get_dispute_backlog(filter: DisputeFilter = Depends(), db: Session = Depends(get_session)):
    query = db.query(MCPScoreDispute)
    if filter.status:
        query = query.filter(MCPScoreDispute.status == filter.status)
    return query.all()

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override get_session for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    client = TestClient(app)

    # Test data setup
    test_dispute = MCPScoreDispute(
        id=1,
        server_id=1,
        submitted_by=1,
        proposed_overall_risk=0.5,
        proposed_axes={"axis1": 0.3, "axis2": 0.7},
        reason_category="test",
        explanation="test explanation",
        status="pending",
        admin_note=None,
        created_at=datetime.now(),
        resolved_at=None
    )

    with TestSession() as session:
        session.add(test_dispute)
        session.commit()

    # Test backlog endpoint
    response = client.get("/disputes/backlog")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["status"] == "pending"

    # Test server disputes endpoint
    response = client.get("/servers/1/disputes")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == 1

    print("PASS")