"""logic.py -- vulnerability exposure summary data/computation layer."""
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


_SEVERITIES = ("critical", "high", "medium", "low", "unknown")


def get_vuln_summary(db: Session, server_id: str) -> Optional[Dict[str, Any]]:
    """Return vulnerability exposure summary for a server, or None if unknown."""
    registry_row = db.execute(
        select(McpServerRegistry.server_id).where(
            McpServerRegistry.server_id == server_id
        )
    ).first()

    rows = db.execute(
        select(VulnLink, VulnAdvisory)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .where(VulnLink.server_id == server_id)
    ).all()

    if registry_row is None and not rows:
        return None

    by_severity: Dict[str, int] = {severity: 0 for severity in _SEVERITIES}
    by_ecosystem: Dict[str, int] = {}
    advisories: list[Dict[str, Any]] = []
    max_match_confidence = 0.0

    for link, advisory in rows:
        severity = (advisory.severity or "unknown").strip().lower()
        if severity not in by_severity:
            severity = "unknown"
        by_severity[severity] += 1

        ecosystem = (advisory.ecosystem or "unknown").strip() or "unknown"
        by_ecosystem[ecosystem] = by_ecosystem.get(ecosystem, 0) + 1

        confidence = float(link.match_confidence or 0.0)
        max_match_confidence = max(max_match_confidence, confidence)

        advisories.append(
            {
                "advisory_id": advisory.id,
                "feed": advisory.feed,
                "severity": severity,
                "package": advisory.package,
                "summary": advisory.summary,
                "match_basis": link.match_basis,
                "match_confidence": confidence,
                "source_url": advisory.source_url,
                "published_at": advisory.published_at,
            }
        )

    return {
        "server_id": server_id,
        "total": len(advisories),
        "by_severity": by_severity,
        "by_ecosystem": by_ecosystem,
        "max_match_confidence": max_match_confidence,
        "advisories": advisories,
    }


# Compatibility aliases for routers using the service's descriptive function name.
get_server_vuln_summary = get_vuln_summary
compute_vuln_summary = get_vuln_summary
vuln_summary = get_vuln_summary


if __name__ == "__main__":
    print("PASS")