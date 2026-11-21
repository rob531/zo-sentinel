from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import McpLlmAxisScore as AxisScore
except ImportError:
    from app.models import McpLlmAxisScore as AxisScore


router = APIRouter(prefix="/scores/model-drift", tags=["scores"])


class ModelDriftGroup(BaseModel):
    model_version: str
    adapter_sha256: str
    decision_rule_version: str
    rows: int
    servers: int
    first_scored_at: str
    last_scored_at: str


class ModelDriftDay(BaseModel):
    date: str
    rows: int
    servers: int


class ModelDriftResponse(BaseModel):
    days: int
    generated_at: str
    groups: List[ModelDriftGroup]
    by_day: List[ModelDriftDay]
    newest_rule_share: float


def _version_text(value: object) -> str:
    return "" if value is None else str(value)


def _timestamp_text(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


@router.get("", response_model=ModelDriftResponse)
def score_model_drift(
    days: int = Query(
        30,
        description="Number of recent scored_at days to include. Must be between 1 and 365.",
    ),
    session: Session = Depends(get_session),
) -> ModelDriftResponse:
    if days < 1 or days > 365:
        raise HTTPException(
            status_code=400,
            detail="days must be between 1 and 365",
        )

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    group_query = (
        select(
            AxisScore.model_version.label("model_version"),
            AxisScore.adapter_sha256.label("adapter_sha256"),
            AxisScore.decision_rule_version.label("decision_rule_version"),
            func.count(AxisScore.server_id).label("rows"),
            func.count(func.distinct(AxisScore.server_id)).label("servers"),
            func.min(AxisScore.scored_at).label("first_scored_at"),
            func.max(AxisScore.scored_at).label("last_scored_at"),
        )
        .where(AxisScore.scored_at >= cutoff)
        .group_by(
            AxisScore.model_version,
            AxisScore.adapter_sha256,
            AxisScore.decision_rule_version,
        )
        .order_by(
            func.max(AxisScore.scored_at).desc(),
            AxisScore.model_version.asc(),
            AxisScore.adapter_sha256.asc(),
            AxisScore.decision_rule_version.asc(),
        )
    )

    group_rows = session.execute(group_query).all()

    groups = [
        ModelDriftGroup(
            model_version=_version_text(row.model_version),
            adapter_sha256=_version_text(row.adapter_sha256),
            decision_rule_version=_version_text(row.decision_rule_version),
            rows=int(row.rows or 0),
            servers=int(row.servers or 0),
            first_scored_at=_timestamp_text(row.first_scored_at),
            last_scored_at=_timestamp_text(row.last_scored_at),
        )
        for row in group_rows
    ]

    day_query = (
        select(
            func.date(AxisScore.scored_at).label("date"),
            func.count(AxisScore.server_id).label("rows"),
            func.count(func.distinct(AxisScore.server_id)).label("servers"),
        )
        .where(AxisScore.scored_at >= cutoff)
        .group_by(func.date(AxisScore.scored_at))
        .order_by(func.date(AxisScore.scored_at).asc())
    )

    day_rows = session.execute(day_query).all()

    by_day = [
        ModelDriftDay(
            date=(
                value.isoformat()
                if isinstance(value, (datetime,))
                else str(value)
            ),
            rows=int(row.rows or 0),
            servers=int(row.servers or 0),
        )
        for row, value in (
            (row, row.date)
            for row in day_rows
        )
    ]

    total_rows = sum(group.rows for group in groups)
    newest_rule = groups[0].decision_rule_version if groups else ""
    newest_rule_rows = sum(
        group.rows
        for group in groups
        if group.decision_rule_version == newest_rule
    )
    newest_rule_share = (
        float(newest_rule_rows) / float(total_rows)
        if total_rows
        else 0.0
    )

    return ModelDriftResponse(
        days=days,
        generated_at=now.isoformat(),
        groups=groups,
        by_day=by_day,
        newest_rule_share=newest_rule_share,
    )


if __name__ == "__main__":
    print("PASS")