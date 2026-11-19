"""HTTP surface for the axis escalation-rate service."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_escalation_rate

router = APIRouter(prefix="/api", tags=["axis-escalation-rate"])


class AxisEscalationRate(BaseModel):
    axis_name: str
    escalated: int
    total: int
    escalation_rate: float
    top_escalated_to: str | None
    distinct_models: int


class EscalationRateResponse(BaseModel):
    days: int
    generated_at: str
    axes: list[AxisEscalationRate]


@router.get("/axis/escalation-rate", response_model=EscalationRateResponse)
async def read_escalation_rate(
    days: int = 30,
    db: Session = Depends(get_session),
) -> EscalationRateResponse:
    payload = await get_escalation_rate(db=db, days=days)
    return EscalationRateResponse.model_validate(payload)