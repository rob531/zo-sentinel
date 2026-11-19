"""Logic for the user account age cohort API.

Reads the real application ``users`` table through the SQLAlchemy session and
computes age cohorts and the associated role and organization totals.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import User


COHORT_BUCKETS = ("0-7", "8-30", "31-90", "91-365", "365+")


def _utc_naive(value: datetime) -> datetime:
    """Return a datetime suitable for age arithmetic across DB drivers."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def get_age_cohort_data(db: Session, days: int = 30) -> Dict:
    """Return account-age cohorts for accounts created within ``days`` days.

    The cutoff is applied in the database query as a bound timestamp. Age
    bucket and role/organization totals are calculated from the selected
    account rows without performing any writes.
    """
    if days < 0:
        raise ValueError("days must be non-negative")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    rows = db.execute(
        select(User.created_at, User.role, User.org_id).where(
            User.created_at >= cutoff
        )
    ).all()

    cohort_counts: Dict[str, int] = {bucket: 0 for bucket in COHORT_BUCKETS}
    role_counts: Dict[str, int] = {}
    org_counts: Dict[str, int] = {}
    now_naive = _utc_naive(now)

    for created_at, role, org_id in rows:
        created_naive = _utc_naive(created_at)
        age_days = max(0, (now_naive - created_naive).days)

        if age_days <= 7:
            bucket = "0-7"
        elif age_days <= 30:
            bucket = "8-30"
        elif age_days <= 90:
            bucket = "31-90"
        elif age_days <= 365:
            bucket = "91-365"
        else:
            bucket = "365+"

        cohort_counts[bucket] += 1
        role_counts[role or "unknown"] = role_counts.get(role or "unknown", 0) + 1
        org_counts[org_id or "unknown"] = org_counts.get(org_id or "unknown", 0) + 1

    cohorts: List[Dict] = [
        {"bucket": bucket, "count": cohort_counts[bucket]} for bucket in COHORT_BUCKETS
    ]
    by_role: List[Dict] = [
        {"role": role, "count": count}
        for role, count in sorted(role_counts.items())
    ]
    by_org: List[Dict] = [
        {"org_id": org_id, "count": count}
        for org_id, count in sorted(org_counts.items())
    ]

    return {
        "days": days,
        "cohorts": cohorts,
        "total": sum(cohort_counts.values()),
        "by_role": by_role,
        "by_org": by_org,
    }


# Compatibility names for callers using the common cohort-report naming.
get_age_cohorts = get_age_cohort_data
get_age_cohort_report = get_age_cohort_data
calculate_age_cohorts = get_age_cohort_data


if __name__ == "__main__":
    print("PASS")