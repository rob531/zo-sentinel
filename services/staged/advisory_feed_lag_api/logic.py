"""logic.py -- data/computation for advisory_feed_lag_api.

Provides a pure function that aggregates vulnerability advisory feed
statistics from the real `VulnAdvisory` model.  The function is used by the
router and can be exercised directly in the module's __main__ self‑test.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


def advisory_feed_lag(db: Session, days: int) -> Dict[str, Any]:
    """Return lag statistics per advisory feed.

    Args:
        db: An active SQLAlchemy Session.
        days: Staleness threshold in days.

    Returns:
        A dict matching the contract:
        {
            "days": int,
            "feeds": [
                {
                    "feed": str,
                    "advisory_count": int,
                    "latest_published_at": iso8601 | None,
                    "latest_fetched_at": iso8601 | None,
                    "lag_hours": float,
                    "stale_count": int,
                },
                ...
            ],
            "generated_at": iso8601,
        }
    """
    now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=days)

    # Aggregate counts and latest timestamps per feed.
    agg_rows = db.execute(
        select(
            VulnAdvisory.feed,
            func.count(VulnAdvisory.id).label("advisory_count"),
            func.max(VulnAdvisory.published_at).label("latest_published_at"),
            func.max(VulnAdvisory.fetched_at).label("latest_fetched_at"),
        ).group_by(VulnAdvisory.feed)
    ).all()

    # Count stale advisories per feed.
    stale_rows = db.execute(
        select(
            VulnAdvisory.feed,
            func.count(VulnAdvisory.id).label("stale_count"),
        )
        .where(VulnAdvisory.fetched_at < cutoff)
        .group_by(VulnAdvisory.feed)
    ).all()
    stale_map: Dict[str, int] = {feed: cnt for feed, cnt in stale_rows}

    feeds: List[Dict[str, Any]] = []
    for feed, adv_cnt, max_pub, max_fet in agg_rows:
        # Convert timestamps to ISO‑8601 strings (or None).
        latest_published_at = (
            max_pub.replace(tzinfo=datetime.timezone.utc).isoformat()
            if max_pub is not None
            else None
        )
        latest_fetched_at = (
            max_fet.replace(tzinfo=datetime.timezone.utc).isoformat()
            if max_fet is not None
            else None
        )
        # Lag in hours; treat missing timestamps as 0.
        if max_pub is not None and max_fet is not None:
            lag_hours = (max_fet - max_pub).total_seconds() / 3600.0
        else:
            lag_hours = 0.0

        feeds.append(
            {
                "feed": feed,
                "advisory_count": int(adv_cnt),
                "latest_published_at": latest_published_at,
                "latest_fetched_at": latest_fetched_at,
                "lag_hours": lag_hours,
                "stale_count": int(stale_map.get(feed, 0)),
            }
        )

    return {
        "days": days,
        "feeds": feeds,
        "generated_at": now.isoformat(),
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Build an in‑memory SQLite DB using the real model metadata.
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    VulnAdvisory.__table__.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    # Seed test data.
    now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    with SessionLocal() as db:
        # Feed A – 3‑day publication‑to‑fetch gap, stale relative to 1‑day threshold.
        db.add(
            VulnAdvisory(
                id=1,
                feed="feed-a",
                source_url="https://example.com/a1",
                published_at=now - datetime.timedelta(days=5),
                fetched_at=now - datetime.timedelta(days=2),
                severity="high",
                ecosystem="npm",
                package="pkg-a",
            )
        )
        # Feed A – recent advisory, not stale.
        db.add(
            VulnAdvisory(
                id=2,
                feed="feed-a",
                source_url="https://example.com/a2",
                published_at=now - datetime.timedelta(hours=5),
                fetched_at=now - datetime.timedelta(hours=4),
                severity="medium",
                ecosystem="npm",
                package="pkg-b",
            )
        )
        # Feed B – fresh advisory.
        db.add(
            VulnAdvisory(
                id=3,
                feed="feed-b",
                source_url="https://example.com/b1",
                published_at=now - datetime.timedelta(hours=2),
                fetched_at=now - datetime.timedelta(hours=1, minutes=30),
                severity="low",
                ecosystem="pypi",
                package="pkg-c",
            )
        )
        db.commit()

        # Run the logic.
        result = advisory_feed_lag(db, days=1)

    # Assertions per contract.
    assert result["days"] == 1, "days mismatch"
    assert isinstance(result["feeds"], list), "feeds not a list"
    assert len(result["feeds"]) == 2, "expected 2 feed entries"

    # Find the feed with a stale advisory.
    stale_feed = next(
        (f for f in result["feeds"] if f["stale_count"] == 1), None
    )
    assert stale_feed is not None, "stale feed not found"
    assert stale_feed["lag_hours"] > 0, "lag_hours should be > 0 for stale feed"

    print("PASS")