from __future__ import annotations

import inspect
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .logic import build_summary, build_transitions, get_session


router = APIRouter(prefix="/api", tags=["perspectives"])


class PerspectiveSummary(BaseModel):
    perspective_id: str
    name: str
    org_id: str
    event_count: int
    distinct_servers: int
    last_event_at: Optional[Any] = None
    last_snapshot_at: Optional[Any] = None
    snapshot_count: int
    tier_transitions: Dict[str, int] = Field(default_factory=dict)
    unseen_event_count: int


class SummaryResponse(BaseModel):
    days: int
    count: int
    perspectives: List[PerspectiveSummary]


class PerspectiveTransition(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    change_type: str
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    seen: bool
    created_at: Any


class PerspectiveDetailResponse(BaseModel):
    perspective_id: str
    summary: PerspectiveSummary
    events: List[PerspectiveTransition]


async def _resolve(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


@router.get(
    "/perspectives/summary",
    response_model=SummaryResponse,
    summary="Summarise perspective membership drift",
)
async def get_perspective_summary(
    days: int = Query(default=30),
    _session: Any = Depends(get_session),
) -> SummaryResponse:
    if days <= 0:
        raise HTTPException(status_code=400, detail="days must be greater than zero")

    perspectives = await _resolve(build_summary(days))
    return SummaryResponse(
        days=days,
        count=len(perspectives),
        perspectives=perspectives,
    )


@router.get(
    "/perspectives/summary/{perspective_id}",
    response_model=PerspectiveDetailResponse,
    summary="Summarise drift for one perspective",
)
async def get_perspective_summary_detail(
    perspective_id: str,
    days: int = Query(default=30),
    _session: Any = Depends(get_session),
) -> PerspectiveDetailResponse:
    if days <= 0:
        raise HTTPException(status_code=400, detail="days must be greater than zero")

    perspectives = await _resolve(build_summary(days))
    summary = next(
        (
            item
            for item in perspectives
            if str(item.get("perspective_id")) == perspective_id
        ),
        None,
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="Perspective not found")

    events = await _resolve(build_transitions(perspective_id, days))
    return PerspectiveDetailResponse(
        perspective_id=perspective_id,
        summary=summary,
        events=events,
    )


if __name__ == "__main__":
    paths = {route.path for route in router.routes}
    assert "/api/perspectives/summary" in paths
    assert "/api/perspectives/summary/{perspective_id}" in paths
    print("PASS")