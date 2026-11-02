"""HTTP surface for the axis coverage matrix API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_coverage

router = APIRouter(prefix="/api", tags=["axis-coverage"])


class AxisCoverageResponse(BaseModel):
    days: int
    registry_sources: list[dict[str, Any]]
    totals: dict[str, int]


@router.get("/axis-coverage", response_model=AxisCoverageResponse)
def get_axis_coverage(
    days: int = 30,
    registry_source: str | None = None,
    db: Session = Depends(get_session),
) -> AxisCoverageResponse:
    payload = axis_coverage(
        db,
        days=days,
        registry_source=registry_source,
    )
    return AxisCoverageResponse(**payload)