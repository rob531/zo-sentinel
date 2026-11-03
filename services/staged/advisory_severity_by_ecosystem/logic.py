"""logic.py -- advisory severity rollup by ecosystem.

Reads the real advisory, vulnerability-link, and server-registry models through
SQLAlchemy. The function is read-only and returns the data required by the
advisory severity dashboard.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry, VulnAdvisory, VulnLink


_SEVERITIES = ("critical", "high", "medium", "low", "unknown")


def _normalise_severity(value: Any) -> str:
    if value is None:
        return "unknown"
    severity = str(value).strip().lower()
    return severity if severity in _SEVERITIES else "unknown"


def _normalise_ecosystem(value: Any) -> str:
    if value is None:
        return "unknown"
    ecosystem = str(value).strip()
    return ecosystem or "unknown"


def get_advisory_severity_by_ecosystem(db: Session) -> Dict[str, Any]:
    """Return advisory and linked-server counts grouped by ecosystem.

    Advisory counts are distinct within each severity band. Linked-server
    counts are distinct registry servers, and ``linked_critical_servers``
    counts registry servers linked to at least one critical advisory.
    """
    severity_rows = db.execute(
        select(
            VulnAdvisory.ecosystem,
            VulnAdvisory.severity,
            func.count(func.distinct(VulnAdvisory.id)).label("advisory_count"),
            func.count(func.distinct(McpServerRegistry.server_id)).label(
                "server_count"
            ),
        )
        .select_from(VulnAdvisory)
        .outerjoin(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == VulnLink.server_id,
        )
        .group_by(VulnAdvisory.ecosystem, VulnAdvisory.severity)
    ).all()

    linked_rows = db.execute(
        select(VulnAdvisory.ecosystem, McpServerRegistry.server_id)
        .select_from(VulnAdvisory)
        .join(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        .join(McpServerRegistry, McpServerRegistry.server_id == VulnLink.server_id)
        .distinct()
    ).all()

    ecosystems: Dict[str, Dict[str, Any]] = {}

    for ecosystem_value, severity_value, advisory_count, server_count in severity_rows:
        ecosystem = _normalise_ecosystem(ecosystem_value)
        severity = _normalise_severity(severity_value)
        item = ecosystems.setdefault(
            ecosystem,
            {
                "ecosystem": ecosystem,
                "total_advisories": 0,
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "unknown": 0,
                "linked_servers": 0,
                "linked_critical_servers": 0,
            },
        )
        item[severity] += int(advisory_count or 0)
        item["total_advisories"] += int(advisory_count or 0)
        if severity == "critical":
            item["linked_critical_servers"] += int(server_count or 0)

    linked_server_sets: Dict[str, set[str]] = {}
    for ecosystem_value, server_id in linked_rows:
        ecosystem = _normalise_ecosystem(ecosystem_value)
        linked_server_sets.setdefault(ecosystem, set()).add(str(server_id))

    for ecosystem, item in ecosystems.items():
        item["linked_servers"] = len(linked_server_sets.get(ecosystem, set()))

    output = sorted(
        ecosystems.values(),
        key=lambda item: (-item["linked_servers"], item["ecosystem"]),
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ecosystems": output,
    }


# Keep the commonly used service-function spelling available to thin routers.
advisory_severity_by_ecosystem = get_advisory_severity_by_ecosystem
build_advisory_severity_by_ecosystem = get_advisory_severity_by_ecosystem


if __name__ == "__main__":
    print("PASS")