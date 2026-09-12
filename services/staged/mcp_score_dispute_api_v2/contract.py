from fastapi import FastAPI, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from datetime import datetime
from app.db import get_session
from app.models import McpScoreDispute, Org

class DisputeCreate(BaseModel):
    server_id: str
    submitted_by: str
    org_id: int
    proposed_overall_risk: str
    proposed_axes: Dict[str, float]
    reason_category: str
    explanation: str

class DisputeResponse(BaseModel):
    id: int
    server_id: str
    submitted_by: str
    org_id: int
    proposed_overall_risk: str
    proposed_axes: Dict[str, float]
    reason_category: str
    explanation: str
    status: str
    admin_note: Optional[str]
    created_at: datetime
    resolved_at: Optional[datetime]

class DisputeStatus(BaseModel):
    status: str

def get_dispute_by_id(dispute_id: int, db: Session = Depends(get_session)) -> Optional[McpScoreDispute]:
    return db.query(McpScoreDispute).filter(McpScoreDispute.id == dispute_id).first()

def create_dispute(dispute_data: DisputeCreate, db: Session = Depends(get_session)) -> McpScoreDispute:
    # Validate org_id exists
    if not db.query(Org).filter(Org.id == dispute_data.org_id).first():
        raise HTTPException(status_code=404, detail="Organization not found")

    dispute = McpScoreDispute(
        server_id=dispute_data.server_id,
        submitted_by=dispute_data.submitted_by,
        org_id=dispute_data.org_id,
        proposed_overall_risk=dispute_data.proposed_overall_risk,
        proposed_axes=dispute_data.proposed_axes,
        reason_category=dispute_data.reason_category,
        explanation=dispute_data.explanation,
        status="open"
    )
    db.add(dispute)
    db.commit()
    db.refresh(dispute)
    return dispute

def update_dispute_status(dispute_id: int, status_data: DisputeStatus, db: Session = Depends(get_session)) -> McpScoreDispute:
    dispute = get_dispute_by_id(dispute_id, db)
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    dispute.status = status_data.status
    if status_data.status == "resolved":
        dispute.resolved_at = datetime.utcnow()
    else:
        dispute.resolved_at = None

    db.commit()
    return dispute

app = FastAPI()

@app.get("/api/disputes/{dispute_id}", response_model=DisputeResponse)
def read_dispute(dispute_id: int, db: Session = Depends(get_session)):
    dispute = get_dispute_by_id(dispute_id, db)
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")
    return dispute

@app.post("/api/disputes", response_model=DisputeResponse, status_code=status.HTTP_201_CREATED)
def create_dispute_endpoint(dispute_data: DisputeCreate, db: Session = Depends(get_session)):
    return create_dispute(dispute_data, db)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Setup in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Override dependencies for testing
    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Test data
    test_org = Org(id=1, name="Test Org", created_at=datetime.utcnow())
    with SessionLocal() as db:
        db.add(test_org)
        db.commit()

    # Test POST /api/disputes
    test_dispute = {
        "server_id": "test-server-1",
        "submitted_by": "test-user",
        "org_id": 1,
        "proposed_overall_risk": "low",
        "proposed_axes": {"axis1": 0.1, "axis2": 0.2},
        "reason_category": "test",
        "explanation": "Test dispute"
    }
    response = client.post("/api/disputes", json=test_dispute)
    assert response.status_code == 201
    dispute_id = response.json()["id"]

    # Test GET /api/disputes/{id}
    response = client.get(f"/api/disputes/{dispute_id}")
    assert response.status_code == 200
    assert response.json()["id"] == dispute_id
    assert response.json()["status"] == "open"

    print("PASS")