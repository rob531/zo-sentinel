from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServer

try:
    from .logic import get_verdict_age_report as _logic_get_verdict_age_report
except ImportError:
    _logic_get_verdict_age_report = None


router = APIRouter()


class VerdictAgeBucket(BaseModel):
    label: str
    min_hours: int
    max_hours: int
    count: int


class VerdictAgeResponse(BaseModel):
    generated_at: str
    window_hours: int
    total_servers: int
    buckets: List[VerdictAgeBucket]
    by_tier: Dict[str, int]
    stale_count: int
    never_assessed_count: int


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_hours(value: Any, now: datetime) -> Optional[float]:
    assessed_at = _as_utc(value)
    if assessed_at is None:
        return None
    return max(0.0, (now - assessed_at).total_seconds() / 3600.0)


def _bucket_for_age(age: float) -> VerdictAgeBucket:
    if age <= 24:
        return VerdictAgeBucket(
            label="0-24h", min_hours=0, max_hours=24, count=0
        )
    if age <= 72:
        return VerdictAgeBucket(
            label="24-72h", min_hours=24, max_hours=72, count=0
        )
    if age <= 168:
        return VerdictAgeBucket(
            label="72-168h", min_hours=72, max_hours=168, count=0
        )
    return VerdictAgeBucket(
        label="168h+", min_hours=168, max_hours=168, count=0
    )


def _build_report(
    session: Session,
    hours: int,
    tier: Optional[str],
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    table = McpServer.__table__

    statement = select(
        table.c.server_id,
        table.c.risk_tier,
        table.c.last_assessed,
    )
    if tier is not None:
        statement = statement.where(table.c.risk_tier == tier)

    rows = session.execute(statement).all()

    buckets = [
        VerdictAgeBucket(
            label="0-24h", min_hours=0, max_hours=24, count=0
        ),
        VerdictAgeBucket(
            label="24-72h", min_hours=24, max_hours=72, count=0
        ),
        VerdictAgeBucket(
            label="72-168h", min_hours=72, max_hours=168, count=0
        ),
        VerdictAgeBucket(
            label="168h+", min_hours=168, max_hours=168, count=0
        ),
    ]

    by_tier: Dict[str, int] = {}
    stale_count = 0
    never_assessed_count = 0

    for row in rows:
        risk_tier = getattr(row, "risk_tier", None)
        tier_key = str(risk_tier) if risk_tier is not None else "unknown"
        by_tier[tier_key] = by_tier.get(tier_key, 0) + 1

        age = _age_hours(getattr(row, "last_assessed", None), now)
        if age is None:
            never_assessed_count += 1
            continue

        bucket = _bucket_for_age(age)
        for candidate in buckets:
            if candidate.label == bucket.label:
                candidate.count += 1
                break

        if age > hours:
            stale_count += 1

    return {
        "generated_at": now.isoformat(),
        "window_hours": hours,
        "total_servers": len(rows),
        "buckets": [bucket.model_dump() for bucket in buckets],
        "by_tier": by_tier,
        "stale_count": stale_count,
        "never_assessed_count": never_assessed_count,
    }


@router.get(
    "/registry/verdict-age",
    response_model=VerdictAgeResponse,
    summary="Report MCP server verdict freshness",
)
def registry_verdict_age(
    hours: int = Query(default=24, ge=0),
    tier: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return _build_report(session=session, hours=hours, tier=tier)