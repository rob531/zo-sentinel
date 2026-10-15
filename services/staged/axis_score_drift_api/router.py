# services/staged/axis_score_drift_api/router.py
from fastapi import APIRouter, Depends
from app.db import get_session
from sqlalchemy.orm import Session

from .logic import get_score_drift

router = APIRouter(prefix="/api", tags=["axis_score_drift"])


@router.get("/servers/{server_id}/score-drift")
def get_score_drift_endpoint(
    server_id: str,
    session: Session = Depends(get_session),
):
    return get_score_drift(session, server_id)