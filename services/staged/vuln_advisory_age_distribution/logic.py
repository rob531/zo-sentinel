"""logic.py -- data/computation for vuln_advisory_age_distribution service.

Provides a single pure function that receives a SQLAlchemy Session and returns
a dictionary ready to be serialized by the FastAPI router.  All queries are
performed against the real ``VulnAdvisory`` model from ``app.models`` using the
ORM; no raw SQL strings are used.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import VulnAdvisory


def advisory_age_distribution(db: Session) -> Dict:
    """Return aggregated statistics for vulnerability advisories.

    The result dictionary matches the response model expected by the router:

    {
        "total": int,
        "by_severity": List[Dict[str, int]],
        "by_ecosystem": List[Dict[str, int]],
        "age_buckets": List[Dict[str, int]],
        "distinct_packages": int,
        "feeds": List[str],
    }
    """
    # ----- total count -------------------------------------------------
    total = db.execute(select(func.count()).select_from(VulnAdvisory)).scalar_one()

    # ----- group by severity -------------------------------------------
    severity_rows = db.execute(
        select(VulnAdvisory.severity, func.count())
        .group_by(VulnAdvisory.severity)
    ).all()
    by_severity: List[Dict[str, int]] = [
        {"severity": sev or "unknown", "count": int(cnt)} for sev, cnt in severity_rows
    ]

    # ----- group by ecosystem ------------------------------------------
    ecosystem_rows = db.execute(
        select(VulnAdvisory.ecosystem, func.count())
        .group_by(VulnAdvisory.ecosystem)
    ).all()
    by_ecosystem: List[Dict[str, int]] = [
        {"ecosystem": eco or "unknown", "count": int(cnt)} for eco, cnt in ecosystem_rows
    ]

    # ----- distinct packages -------------------------------------------
    distinct_packages = db.execute(
        select(func.count(func.distinct(VulnAdvisory.package)))
    ).scalar_one()

    # ----- distinct feeds ------------------------------------------------
    feed_rows = db.execute(select(func.distinct(VulnAdvisory.feed))).scalars().all()
    feeds: List[str] = [f for f in feed_rows if f is not None]

    # ----- age buckets -------------------------------------------------
    now = datetime.now(timezone.utc)
    bucket_defs = [
        ("0-7d", 0, 7),
        ("8-30d", 8, 30),
        ("31-90d", 31, 90),
        (">90d", 91, None),
    ]
    bucket_counts: Dict[str, int] = {name: 0 for name, _, _ in bucket_defs}

    published_dates = db.execute(
        select(VulnAdvisory.published_at)
    ).scalars().all()

    for pub in published_dates:
        if not pub:
            continue
        # ensure timezone‑aware comparison
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        days = (now - pub).days
        for name, lo, hi in bucket_defs:
            if hi is None:
                if days >= lo:
                    bucket_counts[name] += 1
                    break
            elif lo <= days <= hi:
                bucket_counts[name] += 1
                break

    age_buckets: List[Dict[str, int]] = [
        {"bucket": name, "count": cnt} for name, cnt in bucket_counts.items()
    ]

    return {
        "total": int(total),
        "by_severity": by_severity,
        "by_ecosystem": by_ecosystem,
        "age_buckets": age_buckets,
        "distinct_packages": int(distinct_packages),
        "feeds": feeds,
    }