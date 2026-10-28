from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef
from .logic import get_source_mix

router = APIRouter(prefix="/api")


class SourceMixEntry(BaseModel):
    source: str
    count: int = Field(ge=0)
    distinct_pulses: int = Field(ge=0)
    newest_fetched_at: datetime | None = None


class IndicatorTypeEntry(BaseModel):
    indicator_type: str
    count: int = Field(ge=0)


class ThreatSourceMixResponse(BaseModel):
    total: int = Field(ge=0)
    by_source: list[SourceMixEntry]
    by_indicator_type: list[IndicatorTypeEntry]


@router.get(
    "/threat/source-mix",
    response_model=ThreatSourceMixResponse,
    summary="Summarize threat intelligence references by source and indicator type",
)
def threat_source_mix(
    session: Session = Depends(get_session),
) -> ThreatSourceMixResponse:
    return ThreatSourceMixResponse(**get_source_mix(session))