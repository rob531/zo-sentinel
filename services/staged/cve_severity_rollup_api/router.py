# services/staged/cve_severity_rollup_api/logic.py
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import VulnAdvisory, VulnLink


def get_cve_severity_rollup(session: Session) -> dict[str, dict[str, int]]:
    """Aggregate severity counts per ecosystem from vuln_advisories joined to vuln_links."""
    results = (
        session.query(
            VulnAdvisory.ecosystem,
            VulnAdvisory.severity,
            func.count().label("count"),
        )
        .join(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        .group_by(VulnAdvisory.ecosystem, VulnAdvisory.severity)
        .all()
    )

    rollup: dict[str, dict[str, int]] = {}
    for ecosystem, severity, count in results:
        if ecosystem not in rollup:
            rollup[ecosystem] = {}
        rollup[ecosystem][severity] = count

    return rollup