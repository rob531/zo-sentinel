from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_dispute_resolution_duration

router = APIRouter()

@router.get(...)
def dispute_resolution_duration(status: str = "open", days: int = 30, session: Session = Depends(get_session)):
 return get_dispute_resolution_duration(session, status, days)