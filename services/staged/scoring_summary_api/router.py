# services/staged/scoring_summary_api/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session

from .logic import get_scoring_summary

router = APIRouter(prefix="/api/scoring", tags=["scoring"])

@router.get("/summary")
def scoring_summary_endpoint(session: Session = Depends(get_session)):
    return get_scoring_summary(session)