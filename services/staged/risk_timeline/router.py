from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_timeline, RiskTimelineResponse

router = APIRouter(prefix="/api")


@router.get(
    "/servers/{server_id}/risk-timeline",
    response_model=RiskTimelineResponse,
)
def risk_timeline_endpoint(
    server_id: int,
    session: Session = Depends(get_session),
):
    return get_risk_timeline(server_id, session)