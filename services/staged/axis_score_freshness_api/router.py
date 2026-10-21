from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_axis_freshness as build_axis_freshness


router = APIRouter(prefix="/api")


class AxisFreshnessRow(BaseModel):
    axis: str
    servers_scored: int = Field(ge=0)
    newest_scored_at: datetime | None = None
    oldest_scored_at: datetime | None = None
    median_age_hours: float | None = None
    stale_over_24h_count: int = Field(ge=0)
    stale_over_72h_count: int = Field(ge=0)


class AxisFreshnessResponse(BaseModel):
    generated_at: datetime
    axes: list[AxisFreshnessRow]


def _response_from_logic_result(result: Any) -> AxisFreshnessResponse:
    generated_at = datetime.now(timezone.utc)
    rows: Any = result

    if isinstance(result, tuple) and len(result) == 2:
        generated_at, rows = result
    elif isinstance(result, dict) and "axes" in result:
        generated_at = result.get("generated_at", generated_at)
        rows = result["axes"]

    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        rows = list(rows or [])

    return AxisFreshnessResponse(
        generated_at=generated_at,
        axes=[AxisFreshnessRow.model_validate(row) for row in rows],
    )


@router.get("/axis/freshness", response_model=AxisFreshnessResponse)
def get_axis_freshness(
    session: Session = Depends(get_session),
) -> AxisFreshnessResponse:
    return _response_from_logic_result(build_axis_freshness(session))