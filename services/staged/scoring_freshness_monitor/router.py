# services/_exemplar/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import Server, Metric

router = APIRouter(prefix="/api/example", tags=["example"])

class ExampleResponse(BaseModel):
    total: int
    active: int

@router.get("/status", response_model=ExampleResponse)
def get_status(session: Session = Depends(get_session)):
    # Pure SQL logic
    ...