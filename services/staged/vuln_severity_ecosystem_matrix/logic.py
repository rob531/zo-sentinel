from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


SEVERITIES = ("critical", "high", "medium", "low", "unknown")


def get_severity_ecosystem_matrix(db: Session) -> Dict:
    """Build the severity-by-ecosystem advisory matrix."""
    severity_rows = db.execute(
        select(
            VulnAdvisory.ecosystem,
            VulnAdvisory.severity,
            func.count(),
        )
        .where(
            VulnAdvisory.ecosystem.is_not(None),
            VulnAdvisory.severity.in_(SEVERITIES),
        )
        .group_by(VulnAdvisory.ecosystem, VulnAdvisory.severity)
    ).all()

    counts: Dict[str, Dict[str, int]] = {}
    for ecosystem, severity, count in severity_rows:
        ecosystem_name = str(ecosystem)
        severity_name = str(severity)
        counts.setdefault(ecosystem_name, {})[severity_name] = int(count)

    package_rows = db.execute(
        select(
            VulnAdvisory.ecosystem,
            VulnAdvisory.package,
            func.count(),
        )
        .where(
            VulnAdvisory.ecosystem.is_not(None),
            VulnAdvisory.package.is_not(None),
            VulnAdvisory.severity.in_(SEVERITIES),
        )
        .group_by(VulnAdvisory.ecosystem, VulnAdvisory.package)
        .order_by(VulnAdvisory.ecosystem, func.count().desc(), VulnAdvisory.package)
    ).all()

    packages: Dict[str, List[Dict]] = {}
    for ecosystem, package, count in package_rows:
        ecosystem_name = str(ecosystem)
        packages.setdefault(ecosystem_name, []).append(
            {"package": str(package), "count": int(count)}
        )

    total_advisories = int(
        db.execute(select(func.count()).select_from(VulnAdvisory)).scalar_one()
    )

    ecosystems = []
    for ecosystem in sorted(counts):
        by_severity = {severity: int(counts[ecosystem].get(severity, 0)) for severity in SEVERITIES}
        ecosystems.append(
            {
                "ecosystem": ecosystem,
                "total": int(sum(by_severity.values())),
                "by_severity": by_severity,
                "top_packages": packages.get(ecosystem, [])[:10],
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_advisories": total_advisories,
        "ecosystems": ecosystems,
    }


severity_ecosystem_matrix = get_severity_ecosystem_matrix