from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session

from .logic import get_trust_score_histogram

router = APIRouter(prefix="/api", tags=["registry"])


class TrustScoreBucket(BaseModel):
    band: str
    count: int
    pct: float


class TrustScoreHistogramResponse(BaseModel):
    buckets: list[TrustScoreBucket]
    total: int
    unscored: int
    computed_at: str


@router.get(
    "/registry/trust-score-histogram",
    response_model=TrustScoreHistogramResponse,
)
async def trust_score_histogram(
    min_score: float = Query(default=0.0),
    max_score: float = Query(default=100.0),
    session: Any = Depends(get_session),
) -> Any:
    result = get_trust_score_histogram(session, min_score, max_score)
    if inspect.isawaitable(result):
        result = await result
    return result