from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import derive_tier

router = APIRouter()


def get_db(session: Session = Depends(get_session)) -> Session:
    """Provide a DB session dependency for any future endpoints."""
    return session