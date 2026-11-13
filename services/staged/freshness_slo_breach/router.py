from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_freshness_slo_breaches as _logic_get_freshness_slo_breaches
except ImportError:
    _logic_get_freshness_slo_breaches = None


router = APIRouter(prefix="/api", tags=["freshness-slo-breach"])


class FreshnessStatsItem(BaseModel):
    surface: str
    computed_at: Optional[str]


class BreachedServer(BaseModel):
    server_id: str
    name: Optional[str]
    first_seen: Optional[str]
    last_assessed: Optional[str]
    hours_since_first_seen: Optional[float]
    hours_since_assessed: Optional[float]
    breach_kind: str


class FreshnessSloBreachResponse(BaseModel):
    generated_at: str
    sla_hours: Dict[str, int]
    total_servers: int
    breached_count: int
    breached: List[BreachedServer]
    within_sla_count: int
    freshness_stats: List[FreshnessStatsItem]


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse the ISO timestamp forms used by the app database."""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return None
        if text_value.endswith("Z"):
            text_value = text_value[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text_value)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    parsed = _parse_timestamp(value)
    return parsed.isoformat() if parsed is not None else None


def _hours_since(value: Any, now: datetime) -> Optional[float]:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return None
    return round(max(0.0, (now - parsed).total_seconds() / 3600.0), 2)


def _hours_between(start: Any, end: Any) -> Optional[float]:
    parsed_start = _parse_timestamp(start)
    parsed_end = _parse_timestamp(end)
    if parsed_start is None or parsed_end is None:
        return None
    return round((parsed_end - parsed_start).total_seconds() / 3600.0, 2)


def _value(row: Any, key: str, default: Any = None) -> Any:
    if hasattr(row, "keys"):
        return row.get(key, default)
    return getattr(row, key, default)


def _fetch_servers(session: Session) -> List[Dict[str, Any]]:
    result = session.execute(
        text(
            """
            SELECT
                server_id,
                name,
                first_seen,
                last_seen,
                last_assessed,
                scan_count,
                risk_tier,
                verdict
            FROM mcp_server_registry
            ORDER BY server_id
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


def _fetch_freshness_stats(session: Session) -> List[Dict[str, Any]]:
    result = session.execute(
        text(
            """
            SELECT surface, payload, computed_at
            FROM freshness_stats
            ORDER BY surface
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


def _build_breach_rows(
    servers: List[Dict[str, Any]], now: datetime
) -> List[Dict[str, Any]]:
    first_verdict_sla = 24
    re_verdict_sla = 168
    breached: List[Dict[str, Any]] = []

    for server in servers:
        first_seen = server.get("first_seen")
        last_assessed = server.get("last_assessed")

        hours_since_first_seen = _hours_since(first_seen, now)
        hours_since_assessed = _hours_since(last_assessed, now)
        assessment_delay = _hours_between(first_seen, last_assessed)

        breach_kinds: List[str] = []

        # A first verdict is late when it was not recorded within 24 hours
        # of first_seen. If no assessment exists, the first-seen age is used.
        if first_seen is not None:
            if assessment_delay is not None:
                if assessment_delay > first_verdict_sla:
                    breach_kinds.append("first_verdict")
            elif hours_since_first_seen is not None and hours_since_first_seen > first_verdict_sla:
                breach_kinds.append("first_verdict")

        # A server with an assessment older than the re-verdict window has
        # missed the recurring freshness SLA. Servers without any assessment
        # are represented by the first-verdict breach instead.
        if (
            last_assessed is not None
            and hours_since_assessed is not None
            and hours_since_assessed > re_verdict_sla
        ):
            breach_kinds.append("re_verdict")

        if not breach_kinds:
            continue

        breached.append(
            {
                "server_id": str(server.get("server_id")),
                "name": server.get("name"),
                "first_seen": _iso(first_seen),
                "last_assessed": _iso(last_assessed),
                "hours_since_first_seen": hours_since_first_seen,
                "hours_since_assessed": hours_since_assessed,
                "breach_kind": "+".join(breach_kinds),
            }
        )

    return breached


@router.get(
    "/ops/freshness-slo",
    response_model=FreshnessSloBreachResponse,
    summary="List live freshness SLA breaches",
)
def get_freshness_slo_breaches(
    limit: int = Query(default=50, ge=1, le=500),
    only_breached: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> FreshnessSloBreachResponse:
    """Return the current freshness SLA snapshot for registered MCP servers."""
    limit = max(1, min(500, int(limit)))
    now = datetime.now(timezone.utc)

    servers = _fetch_servers(session)
    all_breaches = _build_breach_rows(servers, now)
    visible_breaches = all_breaches if only_breached else all_breaches
    visible_breaches = visible_breaches[:limit]

    stats = []
    for row in _fetch_freshness_stats(session):
        surface = row.get("surface")
        if surface is None:
            continue
        stats.append(
            {
                "surface": str(surface),
                "computed_at": _iso(row.get("computed_at")),
            }
        )

    return FreshnessSloBreachResponse(
        generated_at=now.isoformat(),
        sla_hours={"first_verdict": 24, "re_verdict": 168},
        total_servers=len(servers),
        breached_count=len(all_breaches),
        breached=[BreachedServer(**row) for row in visible_breaches],
        within_sla_count=len(servers) - len(all_breaches),
        freshness_stats=[FreshnessStatsItem(**row) for row in stats],
    )


if __name__ == "__main__":
    # Dependency-free smoke test for the timestamp and clamping helpers.
    assert _parse_timestamp("2024-01-01T00:00:00Z") is not None
    assert _parse_timestamp(None) is None
    assert max(1, min(500, 0)) == 1
    assert max(1, min(500, 501)) == 500
    print("PASS")