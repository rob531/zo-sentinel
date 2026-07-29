from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import compute_tier

router = APIRouter()


@router.get("/api/risk/tier/{server_id}")
def get_risk_tier(server_id: str, db: Session = Depends(get_session)):
    """Return the risk tier for a given server."""
    return compute_tier(server_id)