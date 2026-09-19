from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from app.db import get_session
from app.models import McpScoreDispute, Org, User, ApiKey
import uuid
import json

router = APIRouter(
    prefix="/api/disputes",
    tags=["dispute_resolution"],
    dependencies=[Depends(OAuth2PasswordBearer(tokenUrl="token"))]
)

class DisputeCreate(BaseModel):
    server_id: str = Field(..., description="The ID of the server being disputed")
    proposed_overall_risk: float = Field(..., description="Proposed overall risk score (0-100)")
    proposed_axes: dict = Field(..., description="Proposed scores for each axis")
    reason_category: str = Field(..., description="Category of the dispute")
    explanation: str = Field(..., description="Detailed explanation of the dispute")

class DisputeResponse(BaseModel):
    id: str
    server_id: str
    proposed_overall_risk: float
    proposed_axes: dict
    reason_category: str
    explanation: str
    status: str
    submitted_by: str
    created_at: datetime
    resolved_at: Optional[datetime] = None
    admin_note: Optional[str] = None

def require_role(roles: List[str]):
    async def _require_role(session: Session = Depends(get_session)):
        # Get current user from JWT
        # In a real implementation, this would be extracted from the token
        # For this example, we'll assume the user is in the session
        user = session.query(User).first()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated"
            )
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {user.role} not authorized"
            )
        return user
    return _require_role

@router.post("/",
             response_model=DisputeResponse,
             status_code=status.HTTP_201_CREATED)
async def create_dispute(
    dispute: DisputeCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_role(["user", "admin"]))
):
    """Create a new dispute"""
    dispute_record = McpScoreDispute(
        id=uuid.uuid4(),
        server_id=uuid.UUID(dispute.server_id),
        proposed_overall_risk=dispute.proposed_overall_risk,
        proposed_axes=json.dumps(dispute.proposed_axes),
        reason_category=dispute.reason_category,
        explanation=dispute.explanation,
        submitted_by=current_user.id,
        status="pending"
    )
    session.add(dispute_record)
    session.commit()
    return DisputeResponse(
        id=str(dispute_record.id),
        server_id=str(dispute_record.server_id),
        proposed_overall_risk=dispute_record.proposed_overall_risk,
        proposed_axes=json.loads(dispute_record.proposed_axes),
        reason_category=dispute_record.reason_category,
        explanation=dispute_record.explanation,
        status=dispute_record.status,
        submitted_by=str(dispute_record.submitted_by),
        created_at=dispute_record.created_at,
        resolved_at=dispute_record.resolved_at,
        admin_note=dispute_record.admin_note
    )

@router.get("/",
            response_model=List[DisputeResponse])
async def list_disputes(
    status: Optional[str] = None,
    server_id: Optional[str] = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_role(["admin"]))
):
    """List disputes with optional filters"""
    query = session.query(McpScoreDispute)

    if status:
        query = query.filter(McpScoreDispute.status == status)
    if server_id:
        query = query.filter(McpScoreDispute.server_id == uuid.UUID(server_id))

    disputes = query.all()
    return [
        DisputeResponse(
            id=str(d.id),
            server_id=str(d.server_id),
            proposed_overall_risk=d.proposed_overall_risk,
            proposed_axes=json.loads(d.proposed_axes),
            reason_category=d.reason_category,
            explanation=d.explanation,
            status=d.status,
            submitted_by=str(d.submitted_by),
            created_at=d.created_at,
            resolved_at=d.resolved_at,
            admin_note=d.admin_note
        )
        for d in disputes
    ]

@router.patch("/{dispute_id}",
              response_model=DisputeResponse)
async def update_dispute(
    dispute_id: str,
    status: str,
    admin_note: Optional[str] = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_role(["admin"]))
):
    """Update dispute status"""
    dispute = session.query(McpScoreDispute).filter(
        McpScoreDispute.id == uuid.UUID(dispute_id)
    ).first()

    if not dispute:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dispute not found"
        )

    dispute.status = status
    if admin_note:
        dispute.admin_note = admin_note
    if status in ["resolved", "rejected"]:
        dispute.resolved_at = datetime.utcnow()

    session.commit()

    return DisputeResponse(
        id=str(dispute.id),
        server_id=str(dispute.server_id),
        proposed_overall_risk=dispute.proposed_overall_risk,
        proposed_axes=json.loads(dispute.proposed_axes),
        reason_category=dispute.reason_category,
        explanation=dispute.explanation,
        status=dispute.status,
        submitted_by=str(dispute.submitted_by),
        created_at=dispute.created_at,
        resolved_at=dispute.resolved_at,
        admin_note=dispute.admin_note
    )

def _run_self_test():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import json

    # Create test database
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Test setup
    db = SessionLocal()
    try:
        # Test create dispute
        test_user = User(id=uuid.uuid4(), email="test@example.com", role="user")
        db.add(test_user)
        db.commit()

        test_dispute = DisputeCreate(
            server_id=str(uuid.uuid4()),
            proposed_overall_risk=50.0,
            proposed_axes={"axis1": 40, "axis2": 60},
            reason_category="technical",
            explanation="Test dispute"
        )

        # Override dependency for testing
        from app.main import app
        app.dependency_overrides[get_session] = lambda: db

        # Test create endpoint
        response = create_dispute(test_dispute, db, test_user)
        assert response.status == "pending"

        # Test list endpoint
        disputes = list_disputes(None, None, db, test_user)
        assert len(disputes) == 1

        # Test update endpoint
        updated = update_dispute(
            response.id, "resolved", "Test resolution", db, test_user
        )
        assert updated.status == "resolved"
        assert updated.resolved_at is not None

        print("PASS")
    finally:
        db.close()
        app.dependency_overrides.clear()

if __name__ == "__main__":
    from app.main import app
    _run_self_test()
