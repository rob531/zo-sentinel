"""logic.py -- advisory severity roll‑up computation.

Provides a pure function that aggregates vulnerability advisories by severity
(and optionally ecosystem) and reports counts of advisories, distinct feeds,
distinct packages, distinct linked servers and the most recent publication
date per severity band.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


def advisory_severity_rollup(
    db: Session, ecosystem: Optional[str] = None
) -> Dict[str, Any]:
    """Aggregate advisories by severity.

    Args:
        db: SQLAlchemy Session.
        ecosystem: Optional ecosystem filter; when ``None`` all ecosystems are
            included.

    Returns:
        Mapping with keys:
            - ``ecosystem`` (the filter value or ``None``)
            - ``severities`` (list of per‑severity aggregates)
            - ``total_advisories`` (int)
            - ``total_linked_servers`` (int)
    """
    adv = VulnAdvisory
    link = VulnLink

    # Base filter
    base_where = []
    if ecosystem is not None:
        base_where.append(adv.ecosystem == ecosystem)

    # Per‑severity aggregation (including linked server count)
    severity_stmt = (
        select(
            func.coalesce(adv.severity, "unknown").label("severity"),
            func.count(adv.id).label("advisories"),
            func.count(func.distinct(adv.feed)).label("feeds"),
            func.count(func.distinct(adv.package)).label("packages"),
            func.max(adv.published_at).label("latest_published_at"),
            func.count(func.distinct(link.server_id)).label("linked_servers"),
        )
        .select_from(adv)
        .outerjoin(link, adv.id == link.advisory_id)
        .where(*base_where)
        .group_by(func.coalesce(adv.severity, "unknown"))
    )

    severity_rows = db.execute(severity_stmt).all()

    severities: List[Dict[str, Any]] = []
    for (
        severity,
        advisories,
        feeds,
        packages,
        latest_published_at,
        linked_servers,
    ) in severity_rows:
        severities.append(
            {
                "severity": severity,
                "advisories": int(advisories),
                "feeds": int(feeds),
                "packages": int(packages),
                "linked_servers": int(linked_servers),
                "latest_published_at": latest_published_at,
            }
        )

    # Totals across all selected advisories
    total_adv_stmt = (
        select(func.count(adv.id))
        .where(*base_where)
    )
    total_advisories = db.execute(total_adv_stmt).scalar_one() or 0

    total_linked_stmt = (
        select(func.count(func.distinct(link.server_id)))
        .select_from(adv)
        .join(link, adv.id == link.advisory_id)
        .where(*base_where)
    )
    total_linked_servers = db.execute(total_linked_stmt).scalar_one() or 0

    return {
        "ecosystem": ecosystem,
        "severities": severities,
        "total_advisories": int(total_advisories),
        "total_linked_servers": int(total_linked_servers),
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the self‑test
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    from app.db import Base  # type: ignore

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as session:
        # Seed advisories
        adv1 = VulnAdvisory(
            id=1,
            feed="feed1",
            summary="summary 1",
            severity="high",
            ecosystem="python",
            package="pkg1",
            affected_ranges="*",
            source_url="http://example.com/1",
            published_at=datetime(2023, 1, 1, 12, 0, 0),
            fetched_at=datetime.utcnow(),
        )
        adv2 = VulnAdvisory(
            id=2,
            feed="feed2",
            summary="summary 2",
            severity="medium",
            ecosystem="python",
            package="pkg2",
            affected_ranges="*",
            source_url="http://example.com/2",
            published_at=datetime(2023, 1, 2, 12, 0, 0),
            fetched_at=datetime.utcnow(),
        )
        adv3 = VulnAdvisory(
            id=3,
            feed="feed1",
            summary="summary 3",
            severity="high",
            ecosystem="go",
            package="pkg3",
            affected_ranges="*",
            source_url="http://example.com/3",
            published_at=datetime(2023, 1, 3, 12, 0, 0),
            fetched_at=datetime.utcnow(),
        )
        session.add_all([adv1, adv2, adv3])

        # Seed links (different servers)
        link1 = VulnLink(
            id=1,
            advisory_id=1,
            server_id=10,
            match_basis="basis1",
            match_confidence=0.9,
            match_value="value1",
            linked_at=datetime.utcnow(),
        )
        link2 = VulnLink(
            id=2,
            advisory_id=2,
            server_id=20,
            match_basis="basis2",
            match_confidence=0.8,
            match_value="value2",
            linked_at=datetime.utcnow(),
        )
        session.add_all([link1, link2])
        session.commit()

        result = advisory_severity_rollup(session)

        assert isinstance(result, dict)
        assert len(result["severities"]) == 2, "expected two severity groups"
        # Verify counts per severity
        sev_map = {s["severity"]: s for s in result["severities"]}
        assert sev_map["high"]["advisories"] == 2, "high severity count mismatch"
        assert sev_map["medium"]["advisories"] == 1, "medium severity count mismatch"
        assert result["total_linked_servers"] == 2, "total linked servers mismatch"

        print("PASS")