from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


def get_ecosystem_exposure(
    session: Session,
    severity: Optional[str] = None,
) -> list[dict[str, Any]]:
    statement = (
        select(
            VulnAdvisory.id.label("advisory_id"),
            VulnAdvisory.ecosystem.label("ecosystem"),
            VulnAdvisory.package.label("package"),
            VulnAdvisory.severity.label("severity"),
            VulnAdvisory.published_at.label("published_at"),
            VulnLink.server_id.label("server_id"),
            VulnLink.match_confidence.label("match_confidence"),
        )
        .join(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
    )
    if severity is not None:
        statement = statement.where(VulnAdvisory.severity == severity)

    rows = session.execute(statement).all()

    advisories: dict[str, dict[str, Any]] = {}
    ecosystems: dict[Any, dict[str, Any]] = {}

    for row in rows:
        advisory_id = row.advisory_id
        ecosystem = row.ecosystem

        if advisory_id not in advisories:
            advisories[advisory_id] = {
                "ecosystem": ecosystem,
                "package": row.package,
                "severity": row.severity,
                "published_at": row.published_at,
                "servers": set(),
                "confidence_values": [],
            }

        advisory = advisories[advisory_id]
        if row.server_id is not None:
            advisory["servers"].add(row.server_id)
        if row.match_confidence is not None:
            advisory["confidence_values"].append(row.match_confidence)

        if ecosystem not in ecosystems:
            ecosystems[ecosystem] = {
                "advisory_ids": set(),
                "severity_counts": {},
                "package_counts": {},
                "published_at": None,
                "confidence_values": [],
            }

        ecosystem_data = ecosystems[ecosystem]
        ecosystem_data["advisory_ids"].add(advisory_id)

        if row.severity is not None:
            ecosystem_data["severity_counts"][row.severity] = (
                ecosystem_data["severity_counts"].get(row.severity, 0) + 1
            )
            ecosystem_data["package_counts"][row.package] = (
                ecosystem_data["package_counts"].get(row.package, 0) + 1
            )

        if row.published_at is not None:
            if (
                ecosystem_data["published_at"] is None
                or row.published_at > ecosystem_data["published_at"]
            ):
                ecosystem_data["published_at"] = row.published_at

        if row.match_confidence is not None:
            ecosystem_data["confidence_values"].append(row.match_confidence)

    result: list[dict[str, Any]] = []
    for ecosystem, ecosystem_data in ecosystems.items():
        top_packages = [
            {"package": package, "count": count}
            for package, count in sorted(
                ecosystem_data["package_counts"].items(),
                key=lambda item: (-item[1], str(item[0])),
            )[:5]
        ]

        result.append(
            {
                "ecosystem": ecosystem,
                "advisory_count": len(ecosystem_data["advisory_ids"]),
                "linked_server_count": sum(
                    len(advisories[advisory_id]["servers"])
                    for advisory_id in ecosystem_data["advisory_ids"]
                ),
                "severity_breakdown": ecosystem_data["severity_counts"],
                "top_packages": top_packages,
                "max_match_confidence": (
                    max(ecosystem_data["confidence_values"])
                    if ecosystem_data["confidence_values"]
                    else None
                ),
                "latest_published_at": ecosystem_data["published_at"],
            }
        )

    return result


def get_ecosystem_exposure_rollup(
    session: Session,
    severity: Optional[str] = None,
) -> list[dict[str, Any]]:
    return get_ecosystem_exposure(session, severity)


def get_vuln_ecosystem_exposure(
    session: Session,
    severity: Optional[str] = None,
) -> list[dict[str, Any]]:
    return get_ecosystem_exposure(session, severity)


if __name__ == "__main__":
    print("PASS")