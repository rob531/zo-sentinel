from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from app.db import get_session
from app.models import MCPScoreDispute
from sqlalchemy.orm import Session
import requests

router = APIRouter()

def get_dispute(server_id: str, status: Optional[str] = None, limit: int = 100, offset: int = 0, db: Session = Depends(get_session)) -> List[dict]:
    query = db.query(MCPScoreDispute).filter(MCPScoreDispute.server_id == server_id)

    if status:
        query = query.filter(MCPScoreDispute.status == status)

    disputes = query.limit(limit).offset(offset).all()

    return [
        {
            "id": dispute.id,
            "server_id": dispute.server_id,
            "submitted_by": dispute.submitted_by,
            "proposed_overall_risk": dispute.proposed_overall_risk,
            "proposed_axes": dispute.proposed_axes,
            "reason_category": dispute.reason_category,
            "explanation": dispute.explanation,
            "status": dispute.status,
            "created_at": dispute.created_at,
            "resolved_at": dispute.resolved_at
        }
        for dispute in disputes
    ]

router.get("/servers/{server_id}/disputes")(get_dispute)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Create in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Add test data
    session = SessionLocal()
    test_dispute = MCPScoreDispute(
        server_id="test123",
        submitted_by="test_user",
        proposed_overall_risk=0.5,
        proposed_axes={"axis1": 0.3, "axis2": 0.7},
        reason_category="test_category",
        explanation="test explanation",
        status="open"
    )
    session.add(test_dispute)
    session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/servers/test123/disputes")
    assert len(response.json()) == 1
    assert response.json()[0]["status"] == "open"

    print("PASS")