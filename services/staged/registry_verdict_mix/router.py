"""HTTP surface for the registry verdict mix service."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

try:
    from .logic import registry_verdict_mix as _logic_registry_verdict_mix
except ImportError:
    _logic_registry_verdict_mix = None


router = APIRouter(prefix="/api/registry/verdict-mix", tags=["registry"])


class VerdictCount(BaseModel):
    verdict: str
    count: int
    pct: float


class RiskTierCount(BaseModel):
    risk_tier: str
    count: int
    pct: float


class DailyCount(BaseModel):
    date: str
    count: int
    top_verdict: str


class SourceCount(BaseModel):
    registry_source: str
    count: int


class RegistryTotals(BaseModel):
    servers: int
    scored: int
    unscored: int


class RegistryVerdictMixResponse(BaseModel):
    days: int
    as_of: str
    totals: RegistryTotals
    by_verdict: list[VerdictCount]
    by_risk_tier: list[RiskTierCount]
    daily: list[DailyCount]
    by_source: list[SourceCount]


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket(value: str | None) -> str:
    return value if value is not None and value != "" else "UNSCORED"


def _percentage(count: int, total: int) -> float:
    if total == 0:
        return 0.0
    return round((count / total) * 100.0, 2)


def _calculate_registry_verdict_mix(db: Session, days: int) -> dict[str, Any]:
    registry_rows = list(db.execute(select(McpServerRegistry)).scalars())
    total = len(registry_rows)
    verdicts = Counter(_bucket(row.verdict) for row in registry_rows)
    risk_tiers = Counter(_bucket(row.risk_tier) for row in registry_rows)
    sources = Counter(row.registry_source or "UNKNOWN" for row in registry_rows)

    scored = sum(1 for row in registry_rows if row.verdict not in (None, ""))
    unscored = total - scored
    verdict_by_server = {
        row.server_id: _bucket(row.verdict)
        for row in registry_rows
    }

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    score_rows = list(
        db.execute(
            select(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.scored_at,
            )
        ).all()
    )

    daily_servers: dict[str, set[str]] = defaultdict(set)
    for server_id, scored_at in score_rows:
        assessed = _utc(scored_at)
        if assessed is None or assessed < cutoff or assessed > now:
            continue
        daily_servers[assessed.date().isoformat()].add(server_id)

    if not daily_servers:
        for row in registry_rows:
            assessed = _utc(row.last_assessed)
            if assessed is None or assessed < cutoff or assessed > now:
                continue
            daily_servers[assessed.date().isoformat()].add(row.server_id)

    daily: list[dict[str, Any]] = []
    for date in sorted(daily_servers):
        servers = daily_servers[date]
        daily_verdicts = Counter(
            verdict_by_server.get(server_id, "UNSCORED")
            for server_id in servers
        )
        top_verdict = sorted(
            daily_verdicts,
            key=lambda verdict: (-daily_verdicts[verdict], verdict),
        )[0]
        daily.append(
            {
                "date": date,
                "count": len(servers),
                "top_verdict": top_verdict,
            }
        )

    by_verdict = [
        {
            "verdict": verdict,
            "count": count,
            "pct": _percentage(count, total),
        }
        for verdict, count in sorted(verdicts.items())
    ]
    by_risk_tier = [
        {
            "risk_tier": risk_tier,
            "count": count,
            "pct": _percentage(count, total),
        }
        for risk_tier, count in sorted(risk_tiers.items())
    ]
    by_source = [
        {"registry_source": source, "count": count}
        for source, count in sorted(sources.items())
    ]

    return {
        "days": days,
        "as_of": now.isoformat(),
        "totals": {
            "servers": total,
            "scored": scored,
            "unscored": unscored,
        },
        "by_verdict": by_verdict,
        "by_risk_tier": by_risk_tier,
        "daily": daily,
        "by_source": by_source,
    }


@router.get("", response_model=RegistryVerdictMixResponse)
def get_registry_verdict_mix(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> RegistryVerdictMixResponse:
    bounded_days = max(1, min(365, days))
    if _logic_registry_verdict_mix is not None:
        result = _logic_registry_verdict_mix(db, bounded_days)
    else:
        result = _calculate_registry_verdict_mix(db, bounded_days)
    return RegistryVerdictMixResponse.model_validate(result)


if __name__ == "__main__":
    print("PASS")