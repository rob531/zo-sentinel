"""HTTP surface for verdict-axis consistency checks."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_verdict_axis_consistency

router = APIRouter(prefix="/api/consistency", tags=["consistency"])


class VerdictAxisFlag(BaseModel):
    server_id: str
    verdict: str
    risk_tier: str
    axis_name: str
    label: str
    p_critical: float
    escalated: bool
    reason: str


class VerdictAxisConsistencyResponse(BaseModel):
    checked_servers: int
    flagged_count: int
    flags: list[VerdictAxisFlag]


def _normalise_result(result: Any) -> tuple[int, list[dict[str, Any]]]:
    if isinstance(result, dict):
        flags = result.get("flags", [])
        checked_servers = result.get("checked_servers")
        flagged_count = result.get("flagged_count")
        if checked_servers is None:
            checked_servers = len({flag.get("server_id") for flag in flags})
        if flagged_count is None:
            flagged_count = len(flags)
        return int(checked_servers), list(flags)

    if isinstance(result, tuple) and len(result) == 2:
        checked_servers, flags = result
        return int(checked_servers), list(flags)

    flags = list(result)
    return len({flag.get("server_id") for flag in flags}), flags


@router.get(
    "/verdict-axis",
    response_model=VerdictAxisConsistencyResponse,
)
def get_verdict_axis_consistency_api(
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_session),
) -> VerdictAxisConsistencyResponse:
    result = get_verdict_axis_consistency(db, limit=limit)
    checked_servers, raw_flags = _normalise_result(result)

    flags: list[VerdictAxisFlag] = []
    for raw_flag in raw_flags:
        flag = dict(raw_flag)
        if "label" not in flag and "axis_label" in flag:
            flag["label"] = flag["axis_label"]
        flags.append(VerdictAxisFlag(**flag))

    return VerdictAxisConsistencyResponse(
        checked_servers=checked_servers,
        flagged_count=len(flags),
        flags=flags,
    )