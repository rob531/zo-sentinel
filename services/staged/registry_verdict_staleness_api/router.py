from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api", tags=["registry"])


class VerdictStalenessGroup(BaseModel):
    verdict: Optional[str]
    risk_tier: Optional[str]
    total: int
    stale: int
    stale_pct: float


class VerdictStalenessBucket(VerdictStalenessGroup):
    key: str


class VerdictStalenessResponse(BaseModel):
    max_age_days: int
    generated_at: str
    groups: List[VerdictStalenessGroup]
    buckets: List[VerdictStalenessBucket]


def _clamp_max_age_days(value: int) -> int:
    return max(1, min(3650, int(value)))


def _as_optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _is_stale(value: Any, cutoff: datetime) -> bool:
    assessed_at = _as_datetime(value)
    if assessed_at is None:
        return False

    if assessed_at.tzinfo is None:
        assessed_at = assessed_at.replace(tzinfo=timezone.utc)
    else:
        assessed_at = assessed_at.astimezone(timezone.utc)

    return assessed_at < cutoff


def _compute_groups(
    rows: Iterable[Any], cutoff: datetime
) -> Tuple[List[VerdictStalenessGroup], List[VerdictStalenessBucket]]:
    totals: Dict[Tuple[Optional[str], Optional[str]], Dict[str, int]] = {}

    for row in rows:
        if hasattr(row, "verdict"):
            verdict = _as_optional_string(row.verdict)
            risk_tier = _as_optional_string(row.risk_tier)
            last_assessed = row.last_assessed
        else:
            verdict = _as_optional_string(row[1])
            risk_tier = _as_optional_string(row[2])
            last_assessed = row[3]

        group_key = (verdict, risk_tier)
        group = totals.setdefault(group_key, {"total": 0, "stale": 0})
        group["total"] += 1
        if _is_stale(last_assessed, cutoff):
            group["stale"] += 1

    groups: List[VerdictStalenessGroup] = []
    buckets: List[VerdictStalenessBucket] = []

    for (verdict, risk_tier), counts in sorted(
        totals.items(), key=lambda item: (item[0][0] or "", item[0][1] or "")
    ):
        total = counts["total"]
        stale = counts["stale"]
        stale_pct = round((stale / total) * 100.0, 2) if total else 0.0

        group = VerdictStalenessGroup(
            verdict=verdict,
            risk_tier=risk_tier,
            total=total,
            stale=stale,
            stale_pct=stale_pct,
        )
        groups.append(group)

        key = f"{verdict or 'unknown'}:{risk_tier or 'unknown'}"
        buckets.append(
            VerdictStalenessBucket(
                key=key,
                verdict=verdict,
                risk_tier=risk_tier,
                total=total,
                stale=stale,
                stale_pct=stale_pct,
            )
        )

    return groups, buckets


@router.get(
    "/registry/verdict-staleness",
    response_model=VerdictStalenessResponse,
    summary="Report stale registry verdicts by verdict and risk tier",
)
def get_verdict_staleness(
    max_age_days: int = Query(
        default=30,
        description="Maximum age in days; values are clamped to the range 1..3650.",
    ),
    session=Depends(get_session),
) -> VerdictStalenessResponse:
    max_age_days = _clamp_max_age_days(max_age_days)
    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(days=max_age_days)

    rows = (
        session.query(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
        )
        .all()
    )

    groups, buckets = _compute_groups(rows, cutoff)

    return VerdictStalenessResponse(
        max_age_days=max_age_days,
        generated_at=generated_at.isoformat().replace("+00:00", "Z"),
        groups=groups,
        buckets=buckets,
    )


if __name__ == "__main__":
    from types import SimpleNamespace

    now = datetime.now(timezone.utc)
    sample_rows = [
        SimpleNamespace(
            server_id="server-1",
            verdict="approved",
            risk_tier="low",
            last_assessed=now - timedelta(days=45),
        ),
        SimpleNamespace(
            server_id="server-2",
            verdict="approved",
            risk_tier="low",
            last_assessed=now - timedelta(days=5),
        ),
        SimpleNamespace(
            server_id="server-3",
            verdict="rejected",
            risk_tier="high",
            last_assessed=now - timedelta(days=60),
        ),
    ]

    sample_groups, sample_buckets = _compute_groups(
        sample_rows, now - timedelta(days=30)
    )
    assert len(sample_groups) == 2
    assert sum(group.stale for group in sample_groups) == 2
    assert len(sample_buckets) == 2
    print("PASS")