"""services/staged/ask_answer_feedback/contract.py

FastAPI contract for the ``ask_answer_feedback`` service.

Provides:
* POST /api/ask/feedback   – create a feedback record
* GET  /api/ask/feedback/{feedback_id} – retrieve a feedback record
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Session, relationship

# ----------------------------------------------------------------------
# Real data‑layer imports (must not be stubbed)
# ----------------------------------------------------------------------
from app.db import Base, get_session  # real DB session provider and declarative base
from app.models import User  # existing user model (used for FK)

# ----------------------------------------------------------------------
# SQLAlchemy model for the new feedback table
# ----------------------------------------------------------------------
class AskAnswerFeedback(Base):
    __tablename__ = "ask_answer_feedback"

    id = Column(Integer, primary_key=True, index=True)
    answer_id = Column(Integer, nullable=False)
    server_id = Column(String, nullable=False)
    rating = Column(Integer, nullable=False)          # 1‑5
    comment = Column(String, nullable=True)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    submitter = relationship("User", backref="feedback_entries")


# ----------------------------------------------------------------------
# Pydantic schemas
# ----------------------------------------------------------------------
class FeedbackCreate(BaseModel):
    answer_id: int
    server_id: str
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None
    submitted_by: int

    class Config:
        orm_mode = True


class FeedbackRead(BaseModel):
    id: int
    answer_id: int
    server_id: str
    rating: int
    comment: Optional[str]
    submitted_by: int
    created_at: datetime

    class Config:
        orm_mode = True


# ----------------------------------------------------------------------
# Router definition
# ----------------------------------------------------------------------
router = APIRouter()


@router.post(
    "/api/ask/feedback",
    response_model=FeedbackRead,
    status_code=201,
    tags=["ask_answer_feedback"],
)
def create_feedback(
    payload: FeedbackCreate, db: Session = Depends(get_session)
) -> FeedbackRead:
    """Persist a new feedback record."""
    db_feedback = AskAnswerFeedback(**payload.dict())
    db.add(db_feedback)
    db.commit()
    db.refresh(db_feedback)
    return db_feedback


@router.get(
    "/api/ask/feedback/{feedback_id}",
    response_model=FeedbackRead,
    tags=["ask_answer_feedback"],
)
def read_feedback(feedback_id: int, db: Session = Depends(get_session)) -> FeedbackRead:
    """Retrieve a feedback record by its primary key."""
    fb = db.query(AskAnswerFeedback).filter(AskAnswerFeedback.id == feedback_id).first()
    if not fb:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return fb


# ----------------------------------------------------------------------
# Self‑test (run with ``python -m services.staged.ask_answer_feedback.contract``)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------
    # Build an in‑memory SQLite engine that mimics the real DB
    # ------------------------------------------------------------------
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create all tables (including the new feedback table)
    Base.metadata.create_all(bind=test_engine)

    # ------------------------------------------------------------------
    # Dependency override to use the test session
    # ------------------------------------------------------------------
    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Assemble a minimal FastAPI app for the contract tests
    # ------------------------------------------------------------------
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------
    # Seed a user (required for the FK on submitted_by)
    # ------------------------------------------------------------------
    with TestSessionLocal() as db:
        test_user = User(
            id=1,
            email="tester@example.com",
            role="user",
            org_id=1,
            password_hash="dummyhash",
            created_at=datetime.utcnow(),
        )
        db.add(test_user)
        db.commit()

    # ------------------------------------------------------------------
    # Seed two feedback rows via the API
    # ------------------------------------------------------------------
    seed_payloads = [
        {
            "answer_id": 101,
            "server_id": "srv-A",
            "rating": 4,
            "comment": "Helpful answer",
            "submitted_by": 1,
        },
        {
            "answer_id": 202,
            "server_id": "srv-B",
            "rating": 2,
            "comment": "Needs improvement",
            "submitted_by": 1,
        },
    ]

    for payload in seed_payloads:
        resp = client.post("/api/ask/feedback", json=payload)
        assert resp.status_code == 201, f"seed POST failed: {resp.text}"

    # ------------------------------------------------------------------
    # Verify GET returns the first seeded row correctly
    # ------------------------------------------------------------------
    resp = client.get("/api/ask/feedback/1")
    assert resp.status_code == 200, f"GET failed: {resp.text}"
    data = resp.json()
    assert data["id"] == 1
    assert data["answer_id"] == 101
    assert data["rating"] == 4
    assert data["comment"] == "Helpful answer"

    # ------------------------------------------------------------------
    # Verify POST returns the newly created row with expected ID
    # ------------------------------------------------------------------
    new_payload = {
        "answer_id": 303,
        "server_id": "srv-C",
        "rating": 5,
        "comment": "Excellent!",
        "submitted_by": 1,
    }
    resp = client.post("/api/ask/feedback", json=new_payload)
    assert resp.status_code == 201, f"POST new feedback failed: {resp.text}"
    data = resp.json()
    assert data["id"] == 3
    assert data["rating"] == 5
    assert data["comment"] == "Excellent!"

    print("PASS")