"""services/staged/advisory_severity_mix/logic.py

Data/computation layer for the advisory_severity_mix service.
Provides a single pure function that operates over a SQLAlchemy Session.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import VulnAdvisory


def advisory_severity_mix(
    db: Session, ecosystem: Optional[str] = None
) -> Dict[str, Any]:
    """Return aggregated advisory severity mix.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    ecosystem:
        Optional filter limiting rows to a single ecosystem.

    Returns
    -------
    dict with keys:
        - ecosystem_filter: the supplied filter (or None)
        - severity_counts: mapping severity bucket → count
        - ecosystems: list of {"ecosystem": str, "count": int}
        - feeds: list of {"feed": str, "count": int}
        - total: total number of advisories considered
        - latest_published_at: ISO‑8601 timestamp or None
        - latest_fetched_at: ISO‑8601 timestamp or None
        - stale_share: float proportion of advisories with fetched_at >7 days old
        - generated_at: ISO‑8601 timestamp of generation
    """
    # Base selectable with optional ecosystem filter
    base = select(VulnAdvisory).where(
        True if ecosystem is None else VulnAdvisory.ecosystem == ecosystem
    )
    rows = db.execute(base).scalars().all()

    # Total count
    total = len(rows)

    # Severity bucket aggregation
    severity_buckets = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    for adv in rows:
        sev = (adv.severity or "").lower()
        if sev in severity_buckets:
            severity_buckets[sev] += 1
        else:
            severity_buckets["unknown"] += 1

    # Ecosystem aggregation
    eco_stmt = (
        select(VulnAdvisory.ecosystem, func.count())
        .where(True if ecosystem is None else VulnAdvisory.ecosystem == ecosystem)
        .group_by(VulnAdvisory.ecosystem)
    )
    eco_rows = db.execute(eco_stmt).all()
    ecosystems: List[Dict[str, Any]] = [
        {"ecosystem": eco or "unknown", "count": int(cnt)} for eco, cnt in eco_rows
    ]

    # Feed aggregation
    feed_stmt = (
        select(VulnAdvisory.feed, func.count())
        .where(True if ecosystem is None else VulnAdvisory.ecosystem == ecosystem)
        .group_by(VulnAdvisory.feed)
    )
    feed_rows = db.execute(feed_stmt).all()
    feeds: List[Dict[str, Any]] = [
        {"feed": feed or "unknown", "count": int(cnt)} for feed, cnt in feed_rows
    ]

    # Latest timestamps
    latest_published = (
        db.scalar(select(func.max(VulnAdvisory.published_at)).where(
            True if ecosystem is None else VulnAdvisory.ecosystem == ecosystem
        ))
        if total
        else None
    )
    latest_fetched = (
        db.scalar(select(func.max(VulnAdvisory.fetched_at)).where(
            True if ecosystem is None else VulnAdvisory.ecosystem == ecosystem
        ))
        if total
        else None
    )

    # Stale share (fetched_at older than 7 days)
    threshold = datetime.utcnow() - timedelta(days=7)
    stale_count = (
        db.scalar(
            select(func.count())
            .where(VulnAdvisory.fetched_at < threshold)
            .where(True if ecosystem is None else VulnAdvisory.ecosystem == ecosystem)
        )
        if total
        else 0
    )
    stale_share = stale_count / total if total else 0.0

    return {
        "ecosystem_filter": ecosystem,
        "severity_counts": severity_buckets,
        "ecosystems": ecosystems,
        "feeds": feeds,
        "total": total,
        "latest_published_at": (
            latest_published.isoformat() if latest_published else None
        ),
        "latest_fetched_at": (
            latest_fetched.isoformat() if latest_fetched else None
        ),
        "stale_share": stale_share,
        "generated_at": datetime.utcnow().isoformat(),
    }


# --------------------------------------------------------------------------- #
# Self‑test --------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base  # type: ignore  # Base is the declarative base

    # In‑memory SQLite engine compatible with the app's models
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, future=True)

    # Create tables
    Base.metadata.create_all(engine)

    # Seed test data
    with SessionLocal() as sess:
        now = datetime.utcnow()
        advs = [
            VulnAdvisory(
                id=1,
                feed="feed1",
                summary="c1",
                severity="critical",
                ecosystem="npm",
                package="pkg1",
                source_url="http://example.com/1",
                fetched_at=now,
                published_at=now,
                affected_ranges=None,
                aliases=None,
                identities=None,
                content_hash=None,
            ),
            VulnAdvisory(
                id=2,
                feed="feed1",
                summary="h1",
                severity="high",
                ecosystem="npm",
                package="pkg2",
                source_url="http://example.com/2",
                fetched_at=now,
                published_at=now,
                affected_ranges=None,
                aliases=None,
                identities=None,
                content_hash=None,
            ),
            VulnAdvisory(
                id=3,
                feed="feed2",
                summary="m1",
                severity="medium",
                ecosystem="pypi",
                package="pkg3",
                source_url="http://example.com/3",
                fetched_at=now,
                published_at=now,
                affected_ranges=None,
                aliases=None,
                identities=None,
                content_hash=None,
            ),
            VulnAdvisory(
                id=4,
                feed="feed2",
                summary="u1",
                severity=None,
                ecosystem="pypi",
                package="pkg4",
                source_url="http://example.com/4",
                fetched_at=now - timedelta(days=10),
                published_at=now,
                affected_ranges=None,
                aliases=None,
                identities=None,
                content_hash=None,
            ),
        ]
        sess.add_all(advs)
        sess.commit()

        result = advisory_severity_mix(sess)

        assert result["total"] == 4
        assert result["severity_counts"]["high"] == 1
        assert result["severity_counts"]["unknown"] == 1
        assert len(result["ecosystems"]) == 2
        print("PASS")