"""
Score Disputes API - FastAPI service for MCP score dispute management.
"""
import json
from datetime import datetime
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute, Base

class ScoreDisputeSummary(BaseModel):
    id: int
    server_id: int
    submitted_by: str
    proposed_overall_risk: str
    status: str
    reason_category: str
    created_at: datetime

    class Config:
        from_attributes = True

class ScoreDisputeDetail(BaseModel):
    id: int
    server_id: int
    submitted_by: str
    proposed_overall_risk: str
    proposed_axes: dict
    reason_category: str
    explanation: Optional[str]
    status: str
    admin_note: Optional[str]
    created_at: datetime
    resolved_at: Optional[datetime]

    class Config:
        from_attributes = True

class StatusUpdateResponse(BaseModel):
    id: int
    status: str
    admin_note: Optional[str]
    message: str

    class Config:
        from_attributes = True

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

def create_app() -> FastAPI:
    app = FastAPI(title="Score Disputes API", version="1.0.0", lifespan=lifespan)

    @app.get("/api/score-disputes/", response_model=List[ScoreDisputeSummary])
    def list_disputes(
        skip: int = Query(0, ge=0),
        limit: int = Query(10, ge=1, le=100),
        session: Session = Depends(get_session)
    ):
        disputes = session.query(McpScoreDispute).offset(skip).limit(limit).all()
        return disputes

    @app.get("/api/score-disputes/{dispute_id}", response_model=ScoreDisputeDetail)
    def get_dispute(dispute_id: int, session: Session = Depends(get_session)):
        dispute = session.query(McpScoreDispute).filter(McpScoreDispute.id == dispute_id).first()
        if not dispute:
            raise HTTPException(status_code=404, detail="Dispute not found")
        proposed_axes = dispute.proposed_axes
        if isinstance(proposed_axes, str):
            proposed_axes = json.loads(proposed_axes)
        return ScoreDisputeDetail(
            id=dispute.id,
            server_id=dispute.server_id,
            submitted_by=dispute.submitted_by,
            proposed_overall_risk=dispute.proposed_overall_risk,
            proposed_axes=proposed_axes,
            reason_category=dispute.reason_category,
            explanation=dispute.explanation,
            status=dispute.status,
            admin_note=dispute.admin_note,
            created_at=dispute.created_at,
            resolved_at=dispute.resolved_at
        )

    @app.patch("/api/score-disputes/{dispute_id}", response_model=StatusUpdateResponse)
    def update_dispute_status(
        dispute_id: int,
        status: str = Query(..., regex="^(pending|approved|rejected)$"),
        admin_note: Optional[str] = None,
        session: Session = Depends(get_session)
    ):
        dispute = session.query(McpScoreDispute).filter(McpScoreDispute.id == dispute_id).first()
        if not dispute:
            raise HTTPException(status_code=404, detail="Dispute not found")
        dispute.status = status
        if admin_note is not None:
            dispute.admin_note = admin_note
        session.commit()
        session.refresh(dispute)
        return StatusUpdateResponse(
            id=dispute.id,
            status=dispute.status,
            admin_note=dispute.admin_note,
            message="Status updated successfully"
        )

    return app

if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    with Session(engine) as db:
        db.add(McpScoreDispute(
            id=1, server_id=100, submitted_by="user1",
            proposed_overall_risk="low",
            proposed_axes=json.dumps({"availability": 0.1}),
            reason_category="accuracy", explanation="Risk too high",
            status="pending", admin_note=None,
            created_at=datetime(2024, 1, 1, 12, 0, 0), resolved_at=None
        ))
        db.add(McpScoreDispute(
            id=2, server_id=101, submitted_by="user2",
            proposed_overall_risk="medium",
            proposed_axes=json.dumps({"availability": 0.5}),
            reason_category="outdated", explanation="Data stale",
            status="approved", admin_note="Accepted",
            created_at=datetime(2024, 1, 2, 12, 0, 0),
            resolved_at=datetime(2024, 1, 3, 12, 0, 0)
        ))
        db.add(McpScoreDispute(
            id=3, server_id=102, submitted_by="user3",
            proposed_overall_risk="high",
            proposed_axes=json.dumps({"availability": 0.9}),
            reason_category="methodology", explanation="Wrong calc",
            status="rejected", admin_note="Calc correct",
            created_at=datetime(2024, 1, 3, 12, 0, 0),
            resolved_at=datetime(2024, 1, 4, 12, 0, 0)
        ))
        db.commit()

    response = client.get("/api/score-disputes/")
    assert response.status_code == 200
    assert len(response.json()) == 3

    response = client.patch("/api/score-disputes/2?status=rejected&admin_note=Review%20complete")
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    response = client.get("/api/score-disputes/999")
    assert response.status_code == 404

    print("PASS")