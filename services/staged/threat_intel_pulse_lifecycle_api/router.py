from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_pulse_lifecycle

router = APIRouter(prefix="/api")


class PulseLifecycleItem(BaseModel):
    pulse_id: str
    pulse_name: str | None = None
    first_seen: datetime
    last_seen: datetime
    indicator_count: int = Field(ge=0)
    aggregator_count: int = Field(ge=0)
    indicator_types: dict[str, int] = Field(default_factory=dict)
    age_hours: float = Field(ge=0)


class PulseLifecycleResponse(BaseModel):
    pulses: list[PulseLifecycleItem] = Field(default_factory=list)
    total_pulses: int = Field(ge=0)
    generated_at: datetime


@router.get(
    "/threat-intel/pulses",
    response_model=PulseLifecycleResponse,
    summary="Get threat-intelligence pulse lifecycle information",
)
def get_threat_intel_pulses(
    session: Session = Depends(get_session),
) -> PulseLifecycleResponse:
    return get_pulse_lifecycle(session)


if __name__ == "__main__":
    print("PASS")