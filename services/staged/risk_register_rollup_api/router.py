from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_register_rollup, RollupResponse

router = APIRouter(prefix="/api", tags=["risk_register_rollup"])


@router.get(
    "/risk-register/rollup",
    response_model=RollupResponse,
    summary="Get risk register roll‑up statistics",
)
def risk_register_rollup_endpoint(session: Session = Depends(get_session)):
    """Thin wrapper that forwards the request to the business logic."""
    return get_risk_register_rollup(session)