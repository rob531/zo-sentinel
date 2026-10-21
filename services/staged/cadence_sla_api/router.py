from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_cadence_sla

router = APIRouter(prefix="/api", tags=["cadence_sla"])


@router.get("/cadence/sla")
def cadence_sla_endpoint(
    hours: int = Query(24, ge=1, le=720),
    session: Session = Depends(get_session),
):
    """
    Return SLA health information for Cadence jobs over the past *hours*.
    """
    try:
        return get_cadence_sla(hours, session)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc))