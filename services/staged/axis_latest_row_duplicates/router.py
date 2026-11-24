"""HTTP surface for axis latest-row duplicate reporting."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import axis_latest_row_duplicates

router = APIRouter(prefix="/api", tags=["scoring"])


class AxisDuplicateGroup(BaseModel):
    server_id: str
    axis_name: str
    row_count: int
    latest_count: int
    latest_scored_at: str
    versions: list[str]


class AxisDuplicatesResponse(BaseModel):
    duplicates: list[AxisDuplicateGroup]
    server_count: int


@router.get(
    "/scoring/axis-duplicates",
    response_model=AxisDuplicatesResponse,
)
def get_axis_latest_row_duplicates(
    db: Session = Depends(get_session),
) -> AxisDuplicatesResponse:
    result: Any = axis_latest_row_duplicates(db)

    if isinstance(result, dict):
        duplicates = result.get("duplicates", [])
        server_count = result.get("server_count")
    else:
        duplicates = result
        server_count = None

    groups = [AxisDuplicateGroup.model_validate(group) for group in duplicates]
    if server_count is None:
        server_count = len({group.server_id for group in groups})

    return AxisDuplicatesResponse(
        duplicates=groups,
        server_count=server_count,
    )