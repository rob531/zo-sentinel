from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import compute_risk_tier

router = APIRouter(prefix="/internal")


@router.post("/risk/tier/compute")
def compute_route(session: Session = Depends(get_session)):
    return compute_risk_tier(session)