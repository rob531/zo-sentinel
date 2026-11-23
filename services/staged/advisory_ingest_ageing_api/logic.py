"""logic.py -- data/computation for advisory_ingest_ageing_api.

Provides a single pure function that, given a SQLAlchemy Session, returns a
structure describing the age profile of vulnerability advisories per feed and
the oldest linked advisories. Mirrors the exemplar style.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import VulnAdvisory, VulnLink


def get_advisory_ageing(db: Session) -> Dict[str, Any]:
    """Return ageing statistics for vulnerability advisories.

    The result matches the contract expected by the router:

    {
        "generated_at": "<ISO8601 timestamp>",
        "feeds": [
            {
                "feed": "<feed name>",
                "advisory_count": int,
                "linked_advisory_count": int,
                "unlinked_advisory_count": int,
                "max_age_days": int,
                "mean_age_days": float,
                "bands": {
                    "fresh_7d": int,
                    "stale_30d": int,
                    "aged_90d": int,
                },
            },
            ...
        ],
        "oldest_linked": [
            {
                "advisory_id": int,
                "feed": str,
                "severity": str,
                "summary": str,
                "published_at": datetime,
                "fetched_at": datetime,
                "age_days": int,
                "link_count": int,
            },
            ...
        ],
    }
    """
    now = datetime.utcnow()

    # ------------------------------------------------------------------ #
    # Per‑feed aggregates
    # ------------------------------------------------------------------ #
    feed_rows = db.execute(select(VulnAdvisory.feed).distinct()).scalars().all()
    feeds: List[Dict[str, Any]] = []

    # Pre‑fetch linked advisory ids once for efficiency
    linked_ids_subq = (
        select(VulnLink.advisory_id).distinct()
    ).subquery()
    linked_ids_set = set(
        db.execute(select(linked_ids_subq.c.advisory_id)).scalars().all()
    )

    for feed in feed_rows:
        adv_stmt = select(VulnAdvisory).where(VulnAdvisory.feed == feed)
        advisories = db.execute(adv_stmt).scalars().all()

        advisory_count = len(advisories)

        linked_advisory_ids = {
            adv.id for adv in advisories if adv.id in linked_ids_set
        }
        linked_advisory_count = len(linked_advisory_ids)
        unlinked_advisory_count = advisory_count - linked_advisory_count

        ages = [
            (now - (adv.fetched_at or now)).days for adv in advisories
        ]
        max_age = max(ages) if ages else 0
        mean_age = round(sum(ages) / len(ages), 2) if ages else 0.0

        fresh = sum(1 for a in ages if a <= 7)
        stale = sum(1 for a in ages if 7 < a <= 30)
        aged = sum(1 for a in ages if a > 30)

        feeds.append(
            {
                "feed": feed,
                "advisory_count": advisory_count,
                "linked_advisory_count": linked_advisory_count,
                "unlinked_advisory_count": unlinked_advisory_count,
                "max_age_days": max_age,
                "mean_age_days": mean_age,
                "bands": {
                    "fresh_7d": fresh,
                    "stale_30d": stale,
                    "aged_90d": aged,
                },
            }
        )

    # ------------------------------------------------------------------ #
    # Oldest linked advisories (top 15)
    # ------------------------------------------------------------------ #
    link_counts_subq = (
        select(
            VulnLink.advisory_id,
            func.count(VulnLink.id).label("link_count"),
        )
        .group_by(VulnLink.advisory_id)
        .subquery()
    )

    stmt = (
        select(
            VulnAdvisory.id,
            VulnAdvisory.feed,
            VulnAdvisory.severity,
            VulnAdvisory.summary,
            VulnAdvisory.published_at,
            VulnAdvisory.fetched_at,
            link_counts_subq.c.link_count,
        )
        .join(
            link_counts_subq,
            VulnAdvisory.id == link_counts_subq.c.advisory_id,
        )
    )
    rows = db.execute(stmt).all()

    linked_advisories: List[Dict[str, Any]] = []
    for (
        adv_id,
        feed,
        severity,
        summary,
        published_at,
        fetched_at,
        link_count,
    ) in rows:
        age_days = (now - (fetched_at or now)).days
        linked_advisories.append(
            {
                "advisory_id": adv_id,
                "feed": feed,
                "severity": severity,
                "summary": summary,
                "published_at": published_at,
                "fetched_at": fetched_at,
                "age_days": age_days,
                "link_count": link_count,
            }
        )
    linked_advisories.sort(key=lambda x: x["age_days"], reverse=True)
    oldest_linked = linked_advisories[:15]

    return {
        "generated_at": now.isoformat(),
        "feeds": feeds,
        "oldest_linked": oldest_linked,
    }


# ---------------------------------------------------------------------- #
# Self‑test (executed when run as a script)
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Import the declarative base used by the real models
    from app.db import Base  # type: ignore

    # In‑memory SQLite for the test
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    now = datetime.utcnow()

    # Seed advisories (4 total, across 2 feeds)
    adv1 = VulnAdvisory(
        id=1,
        feed="feed1",
        severity="high",
        summary="Advisory 1",
        published_at=now - timedelta(days=10),
        fetched_at=now - timedelta(days=3),
        content_hash="hash1",
        source_url="http://example.com/1",
    )
    adv2 = VulnAdvisory(
        id=2,
        feed="feed1",
        severity="low",
        summary="Advisory 2",
        published_at=now - timedelta(days=70),
        fetched_at=now - timedelta(days=60),
        content_hash="hash2",
        source_url="http://example.com/2",
    )
    adv3 = VulnAdvisory(
        id=3,
        feed="feed2",
        severity="medium",
        summary="Advisory 3",
        published_at=now - timedelta(days=5),
        fetched_at=now - timedelta(days=5),
        content_hash="hash3",
        source_url="http://example.com/3",
    )
    adv4 = VulnAdvisory(
        id=4,
        feed="feed2",
        severity="medium",
        summary="Advisory 4",
        published_at=now - timedelta(days=20),
        fetched_at=now - timedelta(days=20),
        content_hash="hash4",
        source_url="http://example.com/4",
    )

    # One linked advisory (adv1)
    link1 = VulnLink(
        id=1,
        advisory_id=1,
        server_id=123,
        match_basis="test",
        match_confidence=1.0,
        match_value="value",
    )

    with SessionLocal() as db:
        db.add_all([adv1, adv2, adv3, adv4, link1])
        db.commit()

        result = get_advisory_ageing(db)

        # ---- Assertions per acceptance criteria ----
        assert "generated_at" in result
        feeds = result["feeds"]
        assert len(feeds) == 2, "expected exactly 2 feed entries"

        feed1 = next(f for f in feeds if f["feed"] == "feed1")
        assert feed1["linked_advisory_count"] == 1, "feed1 linked count mismatch"
        assert feed1["unlinked_advisory_count"] == 1, "feed1 unlinked count mismatch"
        assert (
            sum(feed1["bands"].values()) == feed1["advisory_count"]
        ), "band counts do not sum to advisory count for feed1"

        oldest = result["oldest_linked"]
        assert len(oldest) == 1, "expected exactly 1 oldest linked advisory"
        assert oldest[0]["age_days"] >= 0, "age_days should be non‑negative"

        print("PASS")