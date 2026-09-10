# services/_exemplar/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import SomeModel

router = APIRouter()

def execute(param: str, db: Session = Depends(get_session)):
    # implementation
    pass

def get_data(id: int, db: Session = Depends(get_session)):
    # implementation
    pass

__all__ = ["router", "execute", "get_data"]