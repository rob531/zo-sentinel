"""HTTP surface for the vulnerability advisory publication timeline service."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_timeline

router = APIRouter(prefix="/api/vuln/advisories", tags=["vuln-advisories"])


class TimelinePoint(BaseModel):
    date: str
    count: int
    by_severity: dict[str, int]


class TimelineResponse(BaseModel):
    days: int
    ecosystem: str | None
    series: list[TimelinePoint]
    total: int


@router.get("/timeline", response_model=TimelineResponse)
def get_advisory_timeline(
    ecosystem: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> TimelineResponse:
    result = get_timeline(db, ecosystem=ecosystem, days=days)
    if isinstance(result, TimelineResponse):
        return result
    if isinstance(result, Mapping):
        payload: Any = dict(result)
    else:
        payload = {
            "days": result.days,
            "ecosystem": result.ecosystem,
            "series": result.series,
            "total": result.total,
        }
    return TimelineResponse.model_validate(payload)