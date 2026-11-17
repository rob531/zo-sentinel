from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_indicator_type_mix

router = APIRouter(prefix="/api", tags=["threat-intel"])


class IndicatorTypeStat(BaseModel):
    indicator_type: str
    rows: int
    distinct_values: int
    aggregator_rows: int
    oldest_fetched_at: datetime | None
    newest_fetched_at: datetime | None


class IndicatorTypeMixResponse(BaseModel):
    generated_at: datetime
    types: list[IndicatorTypeStat]
    total_rows: int
    distinct_pulses: int


@router.get(
    "/threat-intel/indicator-type-mix",
    response_model=IndicatorTypeMixResponse,
)
def get_indicator_type_mix_endpoint(
    db: Session = Depends(get_session),
) -> IndicatorTypeMixResponse:
    return IndicatorTypeMixResponse(**get_indicator_type_mix(db))