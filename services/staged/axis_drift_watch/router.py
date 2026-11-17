from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_axis_drift_report

router = APIRouter()


@router.get("/api/axis/drift")
def axis_drift(
    server_id: str = Query(..., min_length=1),
    days: int = Query(30, ge=1, le=365),
    threshold: float = Query(0.10, ge=0.0),
    session: Session = Depends(get_session),
):
    return get_axis_drift_report(
        session=session,
        server_id=server_id,
        days=days,
        threshold=threshold,
    )