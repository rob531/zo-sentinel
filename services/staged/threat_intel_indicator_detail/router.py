"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_indicator_detail

router = APIRouter(prefix="/api", tags=["threat-intel"])


class ThreatIntelIndicatorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    indicator_type: str
    indicator_value: str
    pulse_id: str
    pulse_name: str | None
    pulse_created: datetime | None
    is_aggregator: bool
    source: str
    source_url: str
    fetched_at: datetime


@router.get(
    "/threat-intel/indicators/{indicator_id}",
    response_model=ThreatIntelIndicatorResponse,
)
def get_indicator(
    indicator_id: int,
    db: Session = Depends(get_session),
) -> ThreatIntelIndicatorResponse | JSONResponse:
    try:
        indicator = get_indicator_detail(db, indicator_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        raise

    if indicator is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})

    return ThreatIntelIndicatorResponse.model_validate(indicator)