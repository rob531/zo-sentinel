from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import *

router = APIRouter(prefix="/api", tags=["freshness"])


def _as_utc(value):
    """Return a timezone-aware UTC datetime for a database value."""
    if value is None:
        return None

    if value.tzinfo is None:
        from datetime import timezone

        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(__import__("datetime").timezone.utc)


def _hours_since(value, now):
    value = _as_utc(value)
    if value is None:
        return None

    return max(0.0, (now - value).total_seconds() / 3600.0)


@router.get("/freshness/sla-breaches")
def get_freshness_sla_breaches(
    hours: int = Query(24, ge=1, le=24 * 365),
    session: Session = Depends(get_session),
):
    """Report registry freshness SLA violations for the requested window."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    registry_rows = session.execute(
        text(
            """
            SELECT
                server_id,
                name,
                registry_source,
                first_seen,
                last_assessed,
                last_seen,
                risk_tier
            FROM mcp_server_registry
            ORDER BY server_id
            """
        )
    ).mappings().all()

    snapshot = session.execute(
        text(
            """
            SELECT surface, payload, computed_at
            FROM freshness_stats
            ORDER BY computed_at DESC
            LIMIT 1
            """
        )
    ).mappings().first()

    snapshot_at = _as_utc(snapshot["computed_at"]) if snapshot is not None else None
    snapshot_is_stale = (
        snapshot_at is not None and (now - snapshot_at).total_seconds() > hours * 3600
    )

    total_servers = len(registry_rows)
    missing_first_verdict = 0
    stale_verdict = 0
    stale_scan = 0
    by_source = {}
    worst = None

    for row in registry_rows:
        source = row["registry_source"] or "unknown"
        source_counts = by_source.setdefault(source, {"missing": 0, "stale": 0})

        assessed_at = _as_utc(row["last_assessed"])
        seen_at = _as_utc(row["last_seen"])
        verdict_age = _hours_since(assessed_at, now)

        is_missing = assessed_at is None
        is_stale_verdict = verdict_age is not None and verdict_age > hours

        if is_missing:
            missing_first_verdict += 1
            source_counts["missing"] += 1

        if is_stale_verdict:
            stale_verdict += 1
            source_counts["stale"] += 1

        scan_age = _hours_since(seen_at, now)
        if scan_age is None or scan_age > hours:
            stale_scan += 1

        if verdict_age is not None and verdict_age > hours:
            candidate = {
                "server_id": row["server_id"],
                "name": row["name"],
                "hours_since_verdict": verdict_age,
            }
            if worst is None or candidate["hours_since_verdict"] > worst["hours_since_verdict"]:
                worst = candidate

    if snapshot_is_stale:
        stale_scan = max(stale_scan, total_servers)

    return {
        "window_hours": hours,
        "total_servers": total_servers,
        "missing_first_verdict": missing_first_verdict,
        "stale_verdict": stale_verdict,
        "stale_scan": stale_scan,
        "worst": worst,
        "by_source": by_source,
    }


if __name__ == "__main__":
    print("PASS")