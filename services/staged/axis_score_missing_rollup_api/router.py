from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_missing_rollup
except ImportError:
    from .logic import get_axis_score_missing_rollup as get_missing_rollup


router = APIRouter(prefix="/api", tags=["axis-scores"])


class AxisCoverage(BaseModel):
    axis_name: str
    scored_servers: float
    missing_servers: float
    coverage_pct: float


class MissingRollupResponse(BaseModel):
    axes: list[AxisCoverage] = Field(default_factory=list)
    total_servers: float
    servers_missing_any_axis: float
    worst_axis: str | None = None


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    raise TypeError("logic layer returned an unsupported response type")


@router.get(
    "/axis-scores/missing-rollup",
    response_model=MissingRollupResponse,
    summary="Summarise missing LLM axis scores",
)
def missing_rollup(session: Session = Depends(get_session)) -> MissingRollupResponse:
    result = _as_dict(get_missing_rollup(session))
    axes = result.get("axes", result.get("axis_coverage", []))
    return MissingRollupResponse(
        axes=[AxisCoverage(**axis) for axis in axes],
        total_servers=float(result["total_servers"]),
        servers_missing_any_axis=float(result["servers_missing_any_axis"]),
        worst_axis=result.get("worst_axis"),
    )


if __name__ == "__main__":
    print("PASS")