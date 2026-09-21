from fastapi import Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpScoreDispute

class DisputeCreate(BaseModel):
    submitted_by: int
    proposed_overall_risk: str
    explanation: str

def create_dispute(dispute_data: DisputeCreate, session: Session = Depends(get_session)) -> int:
    """Create a new dispute record in the database.

    Args:
        dispute_data: Pydantic model containing dispute details
        session: SQLAlchemy session dependency

    Returns:
        ID of the newly created dispute record

    Raises:
        HTTPException: If the creation fails
    """
    try:
        dispute = McpScoreDispute(
            submitted_by=dispute_data.submitted_by,
            proposed_overall_risk=dispute_data.proposed_overall_risk,
            explanation=dispute_data.explanation
        )
        session.add(dispute)
        session.commit()
        session.refresh(dispute)
        return dispute.id
    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create dispute: {str(e)}"
        )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.test_client import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # Setup test database
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(test_engine)

    # Override dependencies for testing
    def override_get_session():
        session = Session(test_engine)
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    # Include the router that uses this logic
    from services.staged.dispute_resolution import router
    test_app.include_router(router.router)

    client = TestClient(test_app)

    # Test data
    test_dispute = {
        "submitted_by": 1,
        "proposed_overall_risk": "LOW",
        "explanation": "This is a test dispute"
    }

    # Test the endpoint
    response = client.post("/api/disputes", json=test_dispute)
    assert response.status_code == 201
    dispute_id = response.json()["id"]

    # Verify the record exists in the database
    with Session(test_engine) as session:
        dispute = session.query(McpScoreDispute).filter_by(id=dispute_id).first()
        assert dispute is not None
        assert dispute.submitted_by == test_dispute["submitted_by"]
        assert dispute.proposed_overall_risk == test_dispute["proposed_overall_risk"]
        assert dispute.explanation == test_dispute["explanation"]

    print("PASS")