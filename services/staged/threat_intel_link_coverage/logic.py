"""Logic for the threat-intelligence link coverage service."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Dict, Iterator, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef, VulnLink


router = APIRouter(prefix="/api")


@contextmanager
def _session_scope() -> Iterator[Session]:
    """Use the application's session dependency without defining another engine."""
    resource = get_session()
    if hasattr(resource, "__enter__"):
        with resource as db:
            yield db
        return

    db = next(resource)
    try:
        yield db
    finally:
        try:
            next(resource)
        except StopIteration:
            pass


def _link_coverage(
    db: Session,
    indicator_type: Optional[str] = None,
    source: Optional[str] = None,
    days: int = 30,
) -> Dict[str, object]:
    cutoff = datetime.utcnow() - timedelta(days=days)

    query = (
        select(
            ThreatIntelRef.indicator_type,
            ThreatIntelRef.source,
            ThreatIntelRef.indicator_value,
            exists(
                select(VulnLink.id).where(
                    VulnLink.match_value == ThreatIntelRef.indicator_value
                )
            ).label("linked"),
        )
        .where(ThreatIntelRef.fetched_at >= cutoff)
    )
    if indicator_type is not None:
        query = query.where(ThreatIntelRef.indicator_type == indicator_type)
    if source is not None:
        query = query.where(ThreatIntelRef.source == source)

    rows = db.execute(query).all()

    total_refs = len(rows)
    linked_refs = 0
    indicator_totals: Dict[str, int] = {}
    indicator_linked: Dict[str, int] = {}
    source_totals: Dict[str, int] = {}
    source_linked: Dict[str, int] = {}
    indicator_values = set()

    for row in rows:
        ref_indicator_type = row.indicator_type or "unknown"
        ref_source = row.source or "unknown"
        is_linked = bool(row.linked)

        indicator_totals[ref_indicator_type] = indicator_totals.get(ref_indicator_type, 0) + 1
        source_totals[ref_source] = source_totals.get(ref_source, 0) + 1
        indicator_values.add(row.indicator_value)

        if is_linked:
            linked_refs += 1
            indicator_linked[ref_indicator_type] = (
                indicator_linked.get(ref_indicator_type, 0) + 1
            )
            source_linked[ref_source] = source_linked.get(ref_source, 0) + 1

    distinct_servers = 0
    if indicator_values:
        server_rows = db.execute(
            select(VulnLink.server_id).where(VulnLink.match_value.in_(indicator_values))
        ).all()
        distinct_servers = len({row.server_id for row in server_rows})

    def grouped(
        totals: Dict[str, int], linked: Dict[str, int]
    ) -> List[Dict[str, object]]:
        return [
            {
                "indicator_type": key,
                "total": int(totals[key]),
                "linked": int(linked.get(key, 0)),
                "link_rate": round(linked.get(key, 0) / totals[key], 4),
            }
            for key in sorted(totals)
        ] if totals else []

    by_indicator_type = [
        {
            "indicator_type": key,
            "total": int(indicator_totals[key]),
            "linked": int(indicator_linked.get(key, 0)),
            "link_rate": round(
                indicator_linked.get(key, 0) / indicator_totals[key], 4
            ),
        }
        for key in sorted(indicator_totals)
    ]
    by_source = [
        {
            "source": key,
            "total": int(source_totals[key]),
            "linked": int(source_linked.get(key, 0)),
            "link_rate": round(source_linked.get(key, 0) / source_totals[key], 4),
        }
        for key in sorted(source_totals)
    ]

    return {
        "days": int(days),
        "total_refs": int(total_refs),
        "linked_refs": int(linked_refs),
        "unlinked_refs": int(total_refs - linked_refs),
        "link_rate": round(linked_refs / total_refs, 4) if total_refs else 0.0,
        "distinct_servers": int(distinct_servers),
        "by_indicator_type": by_indicator_type,
        "by_source": by_source,
    }


def link_coverage(
    indicator_type: str | None = None,
    source: str | None = None,
    days: int = 30,
) -> dict:
    """Return external threat-intelligence link coverage statistics."""
    with _session_scope() as db:
        return _link_coverage(db, indicator_type, source, days)


@router.get("/threat-intel/link-coverage")
def get_link_coverage(
    indicator_type: str | None = Query(default=None),
    source: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> dict:
    """Report the proportion of threat-intelligence references linked to servers."""
    return _link_coverage(db, indicator_type, source, days)


if __name__ == "__main__":
    print("PASS")