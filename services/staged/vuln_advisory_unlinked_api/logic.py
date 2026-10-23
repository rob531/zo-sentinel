# services/staged/vuln_advisory_unlinked_api/logic.py
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import func, select

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


def _base_query(
    feed: Optional[str] = None,
    severity: Optional[str] = None,
) -> Any:
    """
    Build the core SELECT … LEFT JOIN query that aggregates link counts per advisory.
    """
    stmt = (
        select(
            VulnAdvisory.id.label("advisory_id"),
            VulnAdvisory.feed,
            VulnAdvisory.summary,
            VulnAdvisory.severity,
            VulnAdvisory.ecosystem,
            VulnAdvisory.package,
            VulnAdvisory.published_at,
            VulnAdvisory.source_url,
            func.count(VulnLink.id).label("link_count"),
        )
        .select_from(VulnAdvisory)
        .outerjoin(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
        .group_by(
            VulnAdvisory.id,
            VulnAdvisory.feed,
            VulnAdvisory.summary,
            VulnAdvisory.severity,
            VulnAdvisory.ecosystem,
            VulnAdvisory.package,
            VulnAdvisory.published_at,
            VulnAdvisory.source_url,
        )
    )

    if feed is not None:
        stmt = stmt.where(VulnAdvisory.feed == feed)
    if severity is not None:
        stmt = stmt.where(VulnAdvisory.severity == severity)

    return stmt


def get_unlinked_advisories(
    db: Session,
    *,
    limit: int = 50,
    feed: Optional[str] = None,
    severity: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Return a payload describing vulnerability advisories together with link counts.

    The payload shape matches the contract:

        {
            "count": int,               # total advisories after filters
            "unlinked_count": int,      # advisories with zero links
            "linked_count": int,        # advisories with one or more links
            "advisories": [
                {
                    "advisory_id": int,
                    "feed": str,
                    "summary": str,
                    "severity": str,
                    "ecosystem": str,
                    "package": str,
                    "published_at": datetime,
                    "source_url": str,
                    "link_count": int,
                },
                ...
            ],
        }
    """
    # enforce limits
    limit = max(1, min(limit, 200))

    # total count (after optional filters)
    total_stmt = select(func.count(VulnAdvisory.id))
    if feed is not None:
        total_stmt = total_stmt.where(VulnAdvisory.feed == feed)
    if severity is not None:
        total_stmt = total_stmt.where(VulnAdvisory.severity == severity)
    total_count = db.execute(total_stmt).scalar_one()

    # fetch advisory rows with aggregated link counts
    stmt = _base_query(feed=feed, severity=severity).limit(limit)
    rows = db.execute(stmt).all()

    advisories: List[Dict[str, Any]] = []
    unlinked = 0
    for row in rows:
        adv = {
            "advisory_id": row.advisory_id,
            "feed": row.feed,
            "summary": row.summary,
            "severity": row.severity,
            "ecosystem": row.ecosystem,
            "package": row.package,
            "published_at": row.published_at,
            "source_url": row.source_url,
            "link_count": row.link_count,
        }
        advisories.append(adv)
        if row.link_count == 0:
            unlinked += 1

    linked = total_count - unlinked

    return {
        "count": total_count,
        "unlinked_count": unlinked,
        "linked_count": linked,
        "advisories": advisories,
    }


# Exported symbols for FastAPI routers
__all__ = ["get_unlinked_advisories", "get_session"]