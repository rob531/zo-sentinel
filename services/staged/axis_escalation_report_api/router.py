from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.db import get_session

from .logic import get_axis_escalation_report

router = APIRouter(prefix="/api", tags=["axis-escalations"])


class EscalationTarget(BaseModel):
    escalated_to: str
    count: int = Field(ge=0)


class AxisEscalation(BaseModel):
    axis_name: str
    count: int = Field(ge=0)
    targets: list[EscalationTarget]


class TopEscalatedServer(BaseModel):
    server_id: str
    axis_name: str
    escalated_to: str
    p_top: float


class AxisEscalationReport(BaseModel):
    days: int
    generated_at: datetime
    total_escalations: int = Field(ge=0)
    escalation_rate_pct: float
    by_axis: list[AxisEscalation]
    top_servers: list[TopEscalatedServer]


@router.get("/axes/escalations", response_model=AxisEscalationReport)
def axis_escalations(
    days: int = Query(default=30, ge=1, le=365),
    session=Depends(get_session),
) -> AxisEscalationReport:
    return get_axis_escalation_report(session, days)