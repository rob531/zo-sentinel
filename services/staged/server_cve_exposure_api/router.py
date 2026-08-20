# services/_exemplar/router.py - exemplar pattern
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session

router = APIRouter(prefix="/api", tags=["exemplar"])

@router.get("/example")
def get_example(session: Session = Depends(get_session)):
    return {"status": "ok"}