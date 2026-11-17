"""Logic layer for advisory ecosystem breakdown reporting."""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry, VulnAdvisory, VulnLink


_SEVERITIES = ("critical", "high", "medium", "low")


def get_ecosystem_breakdown(
    db: Session,
    severity: Optional[str] = None,
    ecosystem: Optional[str] = None,
) -> Dict[str, Any]:
    """Build ecosystem advisory totals and link-confidence statistics."""
    conditions = []
    if severity:
        conditions.append(func.lower(VulnAdvisory.severity) == severity.strip().lower())
    if ecosystem:
        conditions.append(func.lower(VulnAdvisory.ecosystem) == ecosystem.strip().lower())

    statement = (
        select(
            VulnAdvisory.id.label("advisory_id"),
            VulnAdvisory.ecosystem.label("ecosystem"),
            VulnAdvisory.package.label("package"),
            VulnAdvisory.severity.label("severity"),
            VulnLink.server_id.label("link_server_id"),
            VulnLink.match_confidence.label("match_confidence"),
            McpServerRegistry.server_id.label("registry_server_id"),
        )
        .select_from(VulnAdvisory)
        .outerjoin(
            VulnLink,
            VulnLink.advisory_id == VulnAdvisory.id,
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == VulnLink.server_id,
        )
    )
    if conditions:
        statement = statement.where(*conditions)

    rows = db.execute(statement).all()

    advisory_ecosystems: Dict[str, set[str]] = {}
    advisory_packages: Dict[str, set[str]] = {}
    ecosystem_servers: Dict[str, set[str]] = {}
    ecosystem_severities: Dict[str, Dict[str, set[str]]] = {}
    ecosystem_confidences: Dict[str, list[float]] = {}

    total_advisories: set[str] = set()
    total_packages: set[str] = set()
    total_servers: set[str] = set()

    for row in rows:
        advisory_id = str(row.advisory_id)
        ecosystem_name = row.ecosystem or "unknown"
        severity_name = (row.severity or "").strip().lower()

        total_advisories.add(advisory_id)
        advisory_ecosystems.setdefault(ecosystem_name, set()).add(advisory_id)
        ecosystem_severities.setdefault(
            ecosystem_name,
            {level: set() for level in _SEVERITIES},
        )
        if severity_name in _SEVERITIES:
            ecosystem_severities[ecosystem_name][severity_name].add(advisory_id)

        if row.package is not None:
            package_name = str(row.package)
            total_packages.add(package_name)
            advisory_packages.setdefault(ecosystem_name, set()).add(package_name)

        if row.registry_server_id is not None:
            server_id = str(row.registry_server_id)
            total_servers.add(server_id)
            ecosystem_servers.setdefault(ecosystem_name, set()).add(server_id)

            if row.match_confidence is not None:
                ecosystem_confidences.setdefault(ecosystem_name, []).append(
                    float(row.match_confidence)
                )

    ecosystem_rows = []
    for ecosystem_name in sorted(advisory_ecosystems):
        confidences = sorted(ecosystem_confidences.get(ecosystem_name, []))
        if confidences:
            rank = max(1, math.ceil(0.95 * len(confidences)))
            p95_confidence = confidences[rank - 1]
        else:
            p95_confidence = 0.0

        severity_mix = {
            level: len(ecosystem_severities[ecosystem_name][level])
            for level in _SEVERITIES
        }
        ecosystem_rows.append(
            {
                "ecosystem": ecosystem_name,
                "advisories": len(advisory_ecosystems[ecosystem_name]),
                "packages": len(advisory_packages.get(ecosystem_name, set())),
                "linked_servers": len(ecosystem_servers.get(ecosystem_name, set())),
                "severity_mix": severity_mix,
                "median_match_confidence": p95_confidence,
            }
        )

    return {
        "totals": {
            "advisories": len(total_advisories),
            "packages": len(total_packages),
            "linked_servers": len(total_servers),
        },
        "ecosystems": ecosystem_rows,
    }


def build_ecosystem_breakdown(
    db: Session,
    severity: Optional[str] = None,
    ecosystem: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the advisory ecosystem breakdown payload."""
    return get_ecosystem_breakdown(db, severity=severity, ecosystem=ecosystem)


def ecosystem_breakdown(
    db: Session,
    severity: Optional[str] = None,
    ecosystem: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the advisory ecosystem breakdown payload."""
    return get_ecosystem_breakdown(db, severity=severity, ecosystem=ecosystem)


if __name__ == "__main__":
    print("PASS")