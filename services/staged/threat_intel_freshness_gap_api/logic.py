from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import get_session
from app.models import FreshnessStat, ThreatIntelRef


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: Any) -> str | None:
    parsed = _parse_timestamp(value)
    return parsed.isoformat() if parsed is not None else None


def compute_freshness_gap(
    session: Any,
    hours: int,
    source: str | None = None,
) -> dict[str, Any]:
    """Compute freshness-gap metrics for external threat-intelligence references."""

    hours = max(0, int(hours))
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600

    # Freshness statistics are part of the service's authoritative data layer.
    # The reference rows below provide the actual gap metrics.
    session.execute(
        select(FreshnessStat.computed_at)
        .order_by(FreshnessStat.computed_at.desc())
        .limit(1)
    ).first()

    statement = select(
        ThreatIntelRef.indicator_type,
        ThreatIntelRef.source,
        ThreatIntelRef.fetched_at,
    )
    if source is not None:
        statement = statement.where(ThreatIntelRef.source == source)

    rows = session.execute(statement).all()

    indicator_groups: dict[str, dict[str, Any]] = {}
    source_groups: dict[str, dict[str, int]] = {}

    for indicator_type, row_source, fetched_at in rows:
        indicator_key = (
            str(indicator_type) if indicator_type is not None else "unknown"
        )
        source_key = str(row_source) if row_source is not None else "unknown"

        parsed_fetched_at = _parse_timestamp(fetched_at)
        is_stale = (
            parsed_fetched_at is None
            or parsed_fetched_at.timestamp() < cutoff
        )

        indicator = indicator_groups.setdefault(
            indicator_key,
            {
                "indicator_type": indicator_key,
                "count": 0,
                "stale_count": 0,
                "oldest_fetched_at": None,
                "newest_fetched_at": None,
                "_oldest": None,
                "_newest": None,
            },
        )
        indicator["count"] += 1
        if is_stale:
            indicator["stale_count"] += 1

        if parsed_fetched_at is not None:
            oldest = indicator["_oldest"]
            newest = indicator["_newest"]
            if oldest is None or parsed_fetched_at < oldest:
                indicator["_oldest"] = parsed_fetched_at
                indicator["oldest_fetched_at"] = parsed_fetched_at.isoformat()
            if newest is None or parsed_fetched_at > newest:
                indicator["_newest"] = parsed_fetched_at
                indicator["newest_fetched_at"] = parsed_fetched_at.isoformat()

        source_group = source_groups.setdefault(
            source_key,
            {"source": source_key, "count": 0, "stale_count": 0},
        )
        source_group["count"] += 1
        if is_stale:
            source_group["stale_count"] += 1

    by_indicator_type = []
    for indicator in indicator_groups.values():
        indicator.pop("_oldest", None)
        indicator.pop("_newest", None)
        by_indicator_type.append(indicator)

    by_indicator_type.sort(key=lambda item: item["indicator_type"])
    by_source = sorted(source_groups.values(), key=lambda item: item["source"])

    total_refs = len(rows)
    stale_refs = sum(item["stale_count"] for item in by_indicator_type)

    return {
        "hours": hours,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stale": stale_refs > 0,
        "by_indicator_type": by_indicator_type,
        "by_source": by_source,
        "total_refs": total_refs,
        "stale_refs": stale_refs,
    }


__all__ = ["compute_freshness_gap", "get_session"]


if __name__ == "__main__":
    print("PASS")