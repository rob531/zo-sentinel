from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_backlog

router = APIRouter(prefix="/api")


@router.get("/disputes/backlog")
def dispute_backlog(session: Session = Depends(get_session)):
    return get_backlog(session)