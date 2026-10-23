"""services/staged/ask_answer_feedback/router.py"""
from .logic import create_feedback, get_feedback
from .schemas import FeedbackCreate, FeedbackResponse
from .models import AskAnswerFeedback
from app.db import get_session
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

router = APIRouter()


@router.post("/api/ask/feedback", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
def submit_feedback(feedback: FeedbackCreate, session: Session = Depends(get_session)) -> FeedbackResponse:
    """Submit user feedback on ask-answer quality."""
    return create_feedback(session, feedback)


@router.get("/api/ask/feedback/{feedback_id}", response_model=FeedbackResponse)
def fetch_feedback(feedback_id: int, session: Session = Depends(get_session)) -> FeedbackResponse:
    """Retrieve a specific feedback entry by ID."""
    result = get_feedback(session, feedback_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback not found")
    return result