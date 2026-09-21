from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_tier_overview

router = APIRouter(prefix="/api")


@router.get("/risk/overview")
def risk_overview(days: int = 7, session: Session = Depends(get_session)):
    """
    Return an overview of risk tier distribution and trends.

    Parameters
    ----------
    days: int
        Number of days to look back for trend calculation.
    session: Session
        Database session provided by FastAPI dependency injection.

    Returns
    -------
    dict
        JSON‑serializable structure containing the overview and trend data.
    """
    try:
        return get_risk_tier_overview(session, days)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc))