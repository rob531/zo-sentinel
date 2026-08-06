from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import ask_answer, AskAnswerRequest

router = APIRouter(prefix="/api/ask", tags=["ask_answer"])


@router.post("/answer")
def answer(request: AskAnswerRequest, db: Session = Depends(get_session)):
    return ask_answer(request, db)