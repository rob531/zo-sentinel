from .logic import compute_risk_tier, run
from fastapi import APIRouter, Depends
from app.db import get_session

router = APIRouter()

__all__ = ["router", "compute_risk_tier", "run"]