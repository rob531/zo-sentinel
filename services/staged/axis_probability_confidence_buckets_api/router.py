from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_session

from .logic import bucket_rows


router = APIRouter(
    prefix="/api",
    tags=["axis_probability_confidence_buckets_api"],
)


class ConfidenceBucket(BaseModel):
    bucket: str
    lower: float
    upper: float
    count: int
    server_count: int


class ConfidenceBucketsResponse(BaseModel):
    days: int
    axis_name: Optional[str]
    buckets: list[ConfidenceBucket]
    total: int


@router.get(
    "/axis/confidence-buckets",
    response_model=ConfidenceBucketsResponse,
)
def get_confidence_buckets(
    axis_name: Optional[str] = Query(default=None),
    days: int = Query(default=30),
    session: Any = Depends(get_session),
) -> ConfidenceBucketsResponse:
    effective_days = max(1, min(365, int(days)))
    cutoff = datetime.utcnow() - timedelta(days=effective_days)

    if axis_name is None:
        statement = text(
            """
            SELECT
                id,
                server_id,
                axis_name,
                label,
                p_top,
                p_critical,
                p_danger,
                escalated,
                decision_rule_version,
                scored_at
            FROM mcp_llm_axis_scores
            WHERE scored_at >= :cutoff
            """
        )
        result = session.execute(statement, {"cutoff": cutoff})
    else:
        statement = text(
            """
            SELECT
                id,
                server_id,
                axis_name,
                label,
                p_top,
                p_critical,
                p_danger,
                escalated,
                decision_rule_version,
                scored_at
            FROM mcp_llm_axis_scores
            WHERE scored_at >= :cutoff
              AND axis_name = :axis_name
            """
        )
        result = session.execute(
            statement,
            {"cutoff": cutoff, "axis_name": axis_name},
        )

    rows = [dict(row) for row in result.mappings().all()]
    buckets = bucket_rows(rows)

    return ConfidenceBucketsResponse(
        days=effective_days,
        axis_name=axis_name,
        buckets=buckets,
        total=sum(int(bucket["count"]) for bucket in buckets),
    )


if __name__ == "__main__":
    print("PASS")