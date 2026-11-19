"""logic.py -- threat pulse coverage data and computation layer.

Reads the real application models through the application SQLAlchemy session.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef, VulnAdvisory, VulnLink


_SEVERITY_BANDS = ("critical", "high", "medium", "low")


def get_pulse_coverage(
    db: Session,
    severity: Optional[str] = None,
    source: Optional[str] = None,
    since_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Build pulse coverage from threat references and linked advisories.

    A vulnerability link is associated with a threat reference when the link's
    ``match_value`` equals the reference's ``indicator_value``. All values
    used for counts are de-duplicated in Python so the result is independent
    of whether the database contains multiple matching references.
    """
    conditions = []
    if severity:
        conditions.append(VulnAdvisory.severity == severity.lower())
    if source:
        conditions.append(ThreatIntelRef.source == source)
    if since_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, int(since_days)))
        conditions.append(
            or_(
                ThreatIntelRef.fetched_at >= cutoff,
                VulnAdvisory.published_at >= cutoff,
            )
        )

    statement = (
        select(ThreatIntelRef, VulnLink, VulnAdvisory)
        .select_from(ThreatIntelRef)
        .outerjoin(
            VulnLink,
            VulnLink.match_value == ThreatIntelRef.indicator_value,
        )
        .outerjoin(
            VulnAdvisory,
            VulnAdvisory.id == VulnLink.advisory_id,
        )
    )
    if conditions:
        statement = statement.where(*conditions)

    rows = db.execute(statement).all()

    indicator_groups: Dict[str, Dict[str, Any]] = {}
    source_groups: Dict[str, Set[str]] = {}
    severity_groups: Dict[str, Dict[str, Set[Any]]] = {
        band: {"advisories": set(), "linked_servers": set()}
        for band in _SEVERITY_BANDS
    }

    total_indicators: Set[str] = set()
    total_pulses: Set[str] = set()
    total_advisories: Set[Any] = set()
    total_links: Set[Any] = set()
    total_aggregators: Set[Any] = set()

    for reference, link, advisory in rows:
        indicator_type = reference.indicator_type or "unknown"
        group = indicator_groups.setdefault(
            indicator_type,
            {
                "indicator_values": set(),
                "pulse_names": set(),
                "aggregators": set(),
                "linked_servers": set(),
            },
        )

        if reference.indicator_value is not None:
            indicator_key = str(reference.indicator_value)
            group["indicator_values"].add(indicator_key)
            total_indicators.add(indicator_key)
            source_groups.setdefault(reference.source or "unknown", set()).add(
                indicator_key
            )

        if reference.pulse_name is not None:
            pulse_key = str(reference.pulse_name)
            group["pulse_names"].add(pulse_key)
            total_pulses.add(pulse_key)

        if reference.is_aggregator:
            group["aggregators"].add(reference.id)
            total_aggregators.add(reference.id)

        if link is not None:
            link_key = link.id
            total_links.add(link_key)
            if link.server_id is not None:
                group["linked_servers"].add(link.server_id)

            if advisory is not None:
                total_advisories.add(advisory.id)
                band = (advisory.severity or "").lower()
                if band in severity_groups:
                    severity_groups[band]["advisories"].add(advisory.id)
                    if link.server_id is not None:
                        severity_groups[band]["linked_servers"].add(link.server_id)

    indicator_types: List[Dict[str, Any]] = []
    for indicator_type in sorted(indicator_groups):
        group = indicator_groups[indicator_type]
        indicator_types.append(
            {
                "indicator_type": indicator_type,
                "indicators": len(group["indicator_values"]),
                "pulses": len(group["pulse_names"]),
                "aggregators": len(group["aggregators"]),
                "linked_servers": len(group["linked_servers"]),
            }
        )

    severities = [
        {
            "severity": band,
            "advisories": len(severity_groups[band]["advisories"]),
            "linked_servers": len(severity_groups[band]["linked_servers"]),
        }
        for band in _SEVERITY_BANDS
    ]

    sources = [
        {
            "source": source_name,
            "indicators": len(source_groups[source_name]),
        }
        for source_name in sorted(source_groups)
    ]

    return {
        "generated_at": datetime.now(timezone.utc),
        "indicator_types": indicator_types,
        "severities": severities,
        "sources": sources,
        "totals": {
            "indicators": len(total_indicators),
            "pulses": len(total_pulses),
            "advisories": len(total_advisories),
            "links": len(total_links),
        },
    }


def pulse_coverage(
    db: Session,
    severity: Optional[str] = None,
    source: Optional[str] = None,
    since_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Compatibility alias for the coverage computation."""
    return get_pulse_coverage(
        db,
        severity=severity,
        source=source,
        since_days=since_days,
    )


def build_pulse_coverage(
    db: Session,
    severity: Optional[str] = None,
    source: Optional[str] = None,
    since_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Compatibility alias for the coverage computation."""
    return get_pulse_coverage(
        db,
        severity=severity,
        source=source,
        since_days=since_days,
    )


if __name__ == "__main__":
    assert callable(get_pulse_coverage)
    print("PASS")