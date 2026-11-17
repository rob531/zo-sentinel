"""logic.py -- vulnerability advisory age-profile computation over the real app models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import VulnAdvisory, VulnLink


SEVERITIES = ("critical", "high", "medium", "low", "unknown")
AGE_BUCKETS = ("0-7", "8-30", "31-90", "91-180", "181-365", "365+")


def _as_datetime(value: Any) -> Optional[datetime]:
    """Normalize ORM or ISO-8601 timestamp values to an aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _reference_time(advisories: Iterable[VulnAdvisory]) -> datetime:
    fetched = [
        value
        for value in (_as_datetime(advisory.fetched_at) for advisory in advisories)
        if value is not None
    ]
    return max(fetched) if fetched else datetime(1970, 1, 1, tzinfo=timezone.utc)


def _age_bucket(age_days: int) -> str:
    if age_days < 0:
        age_days = 0
    if age_days <= 7:
        return "0-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    if age_days <= 180:
        return "91-180"
    if age_days < 365:
        return "181-365"
    return "365+"


def _severity(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in SEVERITIES else "unknown"


def _feed(value: Any) -> str:
    return str(value or "unknown")


def _ecosystem(value: Any) -> str:
    return str(value or "unknown")


def _within_fetch_window(
    fetched_at: Optional[datetime], reference_time: datetime, days: Optional[int]
) -> bool:
    if days is None:
        return True
    if fetched_at is None:
        return True
    return fetched_at >= reference_time - timedelta(days=max(0, days))


def build_age_profile(db: Session, days: Optional[int] = 30) -> Dict[str, Any]:
    """Build an age profile for advisories fetched within the requested window.

    The reference time is the greatest fetched_at value in the advisory table,
    which keeps the result deterministic when the service is offline. Advisory
    age is measured from published_at to that reference time.
    """
    advisories = list(db.execute(select(VulnAdvisory)).scalars().all())
    reference_time = _reference_time(advisories)
    selected = [
        advisory
        for advisory in advisories
        if _within_fetch_window(
            _as_datetime(advisory.fetched_at), reference_time, days
        )
    ]

    bucket_counts = {bucket: 0 for bucket in AGE_BUCKETS}
    severity_counts = {bucket: {severity: 0 for severity in SEVERITIES} for bucket in AGE_BUCKETS}
    feed_counts: Dict[str, int] = {}
    ecosystem_counts: Dict[str, int] = {}

    for advisory in selected:
        published_at = _as_datetime(advisory.published_at)
        age_days = 0
        if published_at is not None:
            age_days = max(0, (reference_time - published_at).days)
        bucket = _age_bucket(age_days)
        bucket_counts[bucket] += 1
        severity_counts[bucket][_severity(advisory.severity)] += 1

        feed = _feed(advisory.feed)
        feed_counts[feed] = feed_counts.get(feed, 0) + 1
        ecosystem = _ecosystem(advisory.ecosystem)
        ecosystem_counts[ecosystem] = ecosystem_counts.get(ecosystem, 0) + 1

    linked_ids: Set[int] = set()
    for link in db.execute(select(VulnLink)).scalars().all():
        advisory_id = getattr(link, "advisory_id", None)
        if advisory_id is not None:
            linked_ids.add(int(advisory_id))

    total = len(selected)
    linked_count = sum(1 for advisory in selected if int(advisory.id) in linked_ids)

    return {
        "days": days if days is not None else 0,
        "reference_time": reference_time.isoformat(),
        "total_advisories": total,
        "age_buckets": [
            {
                "bucket": bucket,
                "count": bucket_counts[bucket],
                "severity": severity_counts[bucket],
            }
            for bucket in AGE_BUCKETS
        ],
        "by_feed": [
            {"feed": feed, "count": count}
            for feed, count in sorted(feed_counts.items())
        ],
        "by_ecosystem": [
            {"ecosystem": ecosystem, "count": count}
            for ecosystem, count in sorted(ecosystem_counts.items())
        ],
        "linked_advisory_count": linked_count,
        "unlinked_advisory_count": total - linked_count,
    }


def get_age_profile(db: Session, days: Optional[int] = 30) -> Dict[str, Any]:
    """Compatibility entry point for callers that use the shorter name."""
    return build_age_profile(db, days)


def age_profile(db: Session, days: Optional[int] = 30) -> Dict[str, Any]:
    """Compatibility entry point for the advisory age-profile service."""
    return build_age_profile(db, days)


if __name__ == "__main__":
    # The endpoint contract exercises this module against the real ORM session.
    # Keep the module-level smoke check limited to the deterministic classifier.
    assert _age_bucket(0) == "0-7"
    assert _age_bucket(7) == "0-7"
    assert _age_bucket(8) == "8-30"
    assert _age_bucket(30) == "8-30"
    assert _age_bucket(31) == "31-90"
    assert _age_bucket(90) == "31-90"
    assert _age_bucket(91) == "91-180"
    assert _age_bucket(180) == "91-180"
    assert _age_bucket(181) == "181-365"
    assert _age_bucket(364) == "181-365"
    assert _age_bucket(365) == "365+"
    print("PASS")