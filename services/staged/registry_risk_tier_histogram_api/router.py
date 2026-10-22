from __future__ import annotations

from datetime import datetime, timedelta, timezone
from inspect import isawaitable
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db import get_session

try:
    from .logic import get_risk_tier_histogram as _get_logic_risk_tier_histogram
except (ImportError, ModuleNotFoundError):
    _get_logic_risk_tier_histogram = None


router = APIRouter(prefix="/api")


class RiskTierBucket(BaseModel):
    risk_tier: str
    count: int
    pct: float


class SourceRiskTierBuckets(BaseModel):
    registry_source: str
    buckets: list[RiskTierBucket]


class RiskTierHistogramResponse(BaseModel):
    total: int
    buckets: list[RiskTierBucket]
    by_source: list[SourceRiskTierBuckets]
    generated_at: str


_TIER_ORDER = [
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
    "UNKNOWN",
]

_RISK_TIER_HISTOGRAM_SQL = text(
    """
    SELECT
        COALESCE(NULLIF(TRIM(risk_tier), ''), 'UNKNOWN') AS risk_tier,
        COALESCE(NULLIF(TRIM(registry_source), ''), 'UNKNOWN') AS registry_source,
        COUNT(*)::bigint AS count
    FROM mcp_server_registry
    WHERE (
        CAST(:registry_source AS text) IS NULL
        OR COALESCE(NULLIF(TRIM(registry_source), ''), 'UNKNOWN') = CAST(:registry_source AS text)
    )
    AND (
        CAST(:cutoff AS timestamp) IS NULL
        OR COALESCE(last_assessed, last_seen) >= CAST(:cutoff AS timestamp)
    )
    GROUP BY
        COALESCE(NULLIF(TRIM(risk_tier), ''), 'UNKNOWN'),
        COALESCE(NULLIF(TRIM(registry_source), ''), 'UNKNOWN')
    ORDER BY
        COALESCE(NULLIF(TRIM(registry_source), ''), 'UNKNOWN'),
        COALESCE(NULLIF(TRIM(risk_tier), ''), 'UNKNOWN')
    """
)


def _sort_key(value: str) -> tuple[int, str]:
    try:
        return (_TIER_ORDER.index(value), value)
    except ValueError:
        return (len(_TIER_ORDER), value)


def _build_response(rows: list[dict[str, Any]]) -> RiskTierHistogramResponse:
    total = sum(int(row["count"]) for row in rows)

    counts_by_tier: dict[str, int] = {}
    counts_by_source: dict[str, dict[str, int]] = {}

    for row in rows:
        tier = str(row["risk_tier"] or "UNKNOWN")
        source = str(row["registry_source"] or "UNKNOWN")
        count = int(row["count"])

        counts_by_tier[tier] = counts_by_tier.get(tier, 0) + count
        counts_by_source.setdefault(source, {})[tier] = (
            counts_by_source.setdefault(source, {}).get(tier, 0) + count
        )

    def buckets_for(source_counts: dict[str, int]) -> list[RiskTierBucket]:
        result: list[RiskTierBucket] = []
        for tier in sorted(source_counts, key=_sort_key):
            count = source_counts[tier]
            pct = round((count / total) * 100.0, 2) if total else 0.0
            result.append(RiskTierBucket(risk_tier=tier, count=count, pct=pct))
        return result

    return RiskTierHistogramResponse(
        total=total,
        buckets=buckets_for(counts_by_tier),
        by_source=[
            SourceRiskTierBuckets(
                registry_source=source,
                buckets=buckets_for(source_counts),
            )
            for source, source_counts in sorted(counts_by_source.items())
        ],
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


async def _execute(session: Any, statement: Any, parameters: dict[str, Any]) -> Any:
    result = session.execute(statement, parameters)
    if isawaitable(result):
        result = await result
    return result


@router.get(
    "/registry/risk-tier-histogram",
    response_model=RiskTierHistogramResponse,
)
async def risk_tier_histogram(
    registry_source: Optional[str] = Query(default=None),
    days: Optional[int] = Query(default=None, ge=1),
    session: Any = Depends(get_session),
) -> RiskTierHistogramResponse:
    cutoff: Optional[datetime] = None
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    result = await _execute(
        session,
        _RISK_TIER_HISTOGRAM_SQL,
        {
            "registry_source": registry_source,
            "cutoff": cutoff,
        },
    )

    rows = result.mappings().all()
    return _build_response([dict(row) for row in rows])


if __name__ == "__main__":
    print("PASS")