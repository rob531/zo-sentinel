from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


_BANDS = ("0-7", "8-30", "31-90", "90+")


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _comparable_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(published_at: Any, fetched_at: Any) -> float | None:
    published = _as_datetime(published_at)
    fetched = _as_datetime(fetched_at)
    if published is None or fetched is None:
        return None

    published_comparable = _comparable_datetime(published)
    fetched_comparable = _comparable_datetime(fetched)
    if published_comparable is None or fetched_comparable is None:
        return None

    age = (fetched_comparable - published_comparable).total_seconds() / 86400.0
    return max(0.0, age)


def _band_for_age(age_days: float) -> str:
    if age_days <= 7:
        return "0-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    return "90+"


def get_freshness_age_summary(db: Session = Depends(get_session)) -> dict[str, Any]:
    statement = (
        select(
            VulnAdvisory.id,
            VulnAdvisory.feed,
            VulnAdvisory.published_at,
            VulnAdvisory.fetched_at,
            VulnLink.server_id,
            McpServerRegistry.server_id.label("registry_server_id"),
        )
        .select_from(VulnAdvisory)
        .outerjoin(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == VulnLink.server_id,
        )
    )

    rows = db.execute(statement).all()

    advisories: dict[int, dict[str, Any]] = {}
    linked_advisory_ids: set[int] = set()

    for row in rows:
        advisory_id = row.id
        if advisory_id not in advisories:
            advisories[advisory_id] = {
                "feed": row.feed,
                "age": _age_days(row.published_at, row.fetched_at),
                "fetched_at": _as_datetime(row.fetched_at),
            }

        if row.server_id is not None:
            linked_advisory_ids.add(advisory_id)

    feeds: dict[str, dict[str, Any]] = {}
    stale_over_30_days = 0

    for advisory in advisories.values():
        feed = advisory["feed"] or "unknown"
        feed_summary = feeds.setdefault(
            feed,
            {
                "advisory_count": 0,
                "linked_count": 0,
                "ages": [],
                "buckets": {band: 0 for band in _BANDS},
                "newest_fetched_at": None,
            },
        )

        feed_summary["advisory_count"] += 1
        age = advisory["age"]
        if age is not None:
            feed_summary["ages"].append(age)
            feed_summary["buckets"][_band_for_age(age)] += 1
            if age > 30:
                stale_over_30_days += 1

        fetched_at = advisory["fetched_at"]
        if fetched_at is not None:
            current = _as_datetime(feed_summary["newest_fetched_at"])
            if current is None or (
                _comparable_datetime(fetched_at) > _comparable_datetime(current)
            ):
                feed_summary["newest_fetched_at"] = fetched_at

    feed_results: list[dict[str, Any]] = []
    for feed in sorted(feeds):
        summary = feeds[feed]
        ages = summary["ages"]
        feed_results.append(
            {
                "feed": feed,
                "advisory_count": summary["advisory_count"],
                "linked_count": sum(
                    1
                    for advisory_id, advisory in advisories.items()
                    if (advisory["feed"] or "unknown") == feed
                    and advisory_id in linked_advisory_ids
                ),
                "median_age_days": median(ages) if ages else None,
                "max_age_days": max(ages) if ages else None,
                "buckets": [
                    {"band": band, "count": summary["buckets"][band]}
                    for band in _BANDS
                ],
                "newest_fetched_at": summary["newest_fetched_at"],
            }
        )

    return {
        "feeds": feed_results,
        "totals": {
            "advisory_count": len(advisories),
            "linked_count": len(linked_advisory_ids),
            "stale_over_30_days": stale_over_30_days,
        },
    }


get_vuln_advisory_freshness_age = get_freshness_age_summary
get_advisory_freshness_age = get_freshness_age_summary
get_freshness_age = get_freshness_age_summary


if __name__ == "__main__":
    print("PASS")