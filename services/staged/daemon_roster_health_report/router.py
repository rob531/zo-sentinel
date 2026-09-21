# services/_exemplar/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session

router = APIRouter(prefix="/api", tags=["exemplar"])


@router.get("/exemplar")
def get_exemplar(session: Session = Depends(get_session)):
    return {"status": "ok"}