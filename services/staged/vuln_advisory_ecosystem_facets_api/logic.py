from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


_SEVERITIES = ("critical", "high", "medium", "low")


def _as_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row

    if hasattr(row, "_mapping"):
        return row._mapping

    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _value(row: Any, *names: str) -> Any:
    if isinstance(row, Mapping):
        for name in names:
            if name in row:
                return row[name]
        return None

    if hasattr(row, "_mapping"):
        mapping = row._mapping
        for name in names:
            if name in mapping:
                return mapping[name]
        return None

    try:
        values = dict(row)
    except (TypeError, ValueError):
        values = {}

    for name in names:
        if name in values:
            return values[name]

    return None


def _nested_value(row: Any, name: str) -> Any:
    value = _value(row, name)
    if value is not None:
        return value

    advisory = _value(row, "advisory")
    if advisory is not None:
        if isinstance(advisory, Mapping):
            return advisory.get(name)
        return getattr(advisory, name, None)

    return None


def _published_at(row: Any) -> datetime | None:
    value = _nested_value(row, "published_at")
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_recent(value: datetime | None, days: int) -> bool:
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return value >= cutoff


def _normalise_severity(value: Any) -> str:
    return str(value or "unknown").strip().lower()


def facet_ecosystem_facets(rows: Iterable[Any], days: int = 30) -> dict[str, Any]:
    """Build ecosystem facets from advisory/link rows without accessing a database."""
    severity_advisories: dict[str, set[str]] = defaultdict(set)
    ecosystem_advisories: dict[str, set[str]] = defaultdict(set)
    ecosystem_servers: dict[str, set[str]] = defaultdict(set)
    ecosystem_confidences: dict[str, list[float]] = defaultdict(list)
    all_advisories: set[str] = set()
    all_servers: set[str] = set()

    for row in rows:
        published_at = _published_at(row)
        if not _is_recent(published_at, days):
            continue

        ecosystem = _nested_value(row, "ecosystem")
        if not ecosystem:
            continue

        advisory_id = _value(row, "advisory_id")
        if advisory_id is None:
            advisory_id = _nested_value(row, "id")
        if advisory_id is None:
            advisory_id = _value(row, "id")
        advisory_key = str(advisory_id)

        severity = _normalise_severity(_nested_value(row, "severity"))
        server_id = _value(row, "server_id")
        confidence = _value(row, "match_confidence")

        ecosystem_key = str(ecosystem)
        ecosystem_advisories[ecosystem_key].add(advisory_key)
        severity_advisories[ecosystem_key].add(advisory_key)
        all_advisories.add(advisory_key)

        if server_id is not None:
            server_key = str(server_id)
            ecosystem_servers[ecosystem_key].add(server_key)
            all_servers.add(server_key)

        if confidence is not None:
            try:
                ecosystem_confidences[ecosystem_key].append(float(confidence))
            except (TypeError, ValueError):
                pass

    facets: list[dict[str, Any]] = []
    for ecosystem in sorted(ecosystem_advisories):
        severity_counts: dict[str, int] = {}
        for severity in _SEVERITIES:
            matching = {
                advisory_id
                for advisory_id in severity_advisories[ecosystem]
                if severity == _normalise_severity(
                    _severity_for_advisory(rows, advisory_id)
                )
            }
            if matching:
                severity_counts[severity] = len(matching)

        confidences = ecosystem_confidences.get(ecosystem, [])
        facets.append(
            {
                "ecosystem": ecosystem,
                "severity_counts": severity_counts,
                "total": len(ecosystem_advisories[ecosystem]),
                "linked_servers": len(ecosystem_servers[ecosystem]),
                "median_match_confidence": (
                    round(float(median(confidences)), 4) if confidences else 0.0
                ),
            }
        )

    return {
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "facets": facets,
        "totals": {
            "advisories": len(all_advisories),
            "linked_servers": len(all_servers),
        },
    }


def _severity_for_advisory(rows: Iterable[Any], advisory_id: str) -> str:
    for row in rows:
        current_id = _value(row, "advisory_id")
        if current_id is None:
            current_id = _nested_value(row, "id")
        if current_id is None:
            current_id = _value(row, "id")
        if str(current_id) == advisory_id:
            return _normalise_severity(_nested_value(row, "severity"))
    return "unknown"


def get_vuln_ecosystem_facets(
    session: Session,
    days: int = 30,
    severity: str | None = None,
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    statement = (
        select(
            VulnAdvisory.id.label("advisory_id"),
            VulnAdvisory.ecosystem.label("ecosystem"),
            VulnAdvisory.severity.label("severity"),
            VulnAdvisory.published_at.label("published_at"),
            VulnLink.server_id.label("server_id"),
            VulnLink.match_confidence.label("match_confidence"),
        )
        .join(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        .where(
            VulnAdvisory.published_at.is_not(None),
            VulnAdvisory.published_at >= cutoff,
        )
        .order_by(VulnAdvisory.id, VulnLink.id)
    )

    if severity:
        statement = statement.where(
            func.lower(VulnAdvisory.severity) == severity.strip().lower()
        )

    rows = session.execute(statement).mappings().all()
    result = facet_ecosystem_facets(rows, days)

    if severity:
        requested = severity.strip().lower()
        for facet in result["facets"]:
            facet["severity_counts"] = {
                requested: facet["severity_counts"].get(requested, 0)
            }

    return result


def get_ecosystem_facets(
    session: Session,
    days: int = 30,
    severity: str | None = None,
) -> dict[str, Any]:
    return get_vuln_ecosystem_facets(session, days=days, severity=severity)


def get_vuln_advisory_ecosystem_facets(
    session: Session,
    days: int = 30,
    severity: str | None = None,
) -> dict[str, Any]:
    return get_vuln_ecosystem_facets(session, days=days, severity=severity)


def get_facets(
    session: Session,
    days: int = 30,
    severity: str | None = None,
) -> dict[str, Any]:
    return get_vuln_ecosystem_facets(session, days=days, severity=severity)


if __name__ == "__main__":
    _rows = [
        {
            "advisory_id": 1,
            "ecosystem": "npm",
            "severity": "CRITICAL",
            "published_at": datetime.now(timezone.utc),
            "server_id": 10,
            "match_confidence": 0.9,
        },
        {
            "advisory_id": 2,
            "ecosystem": "npm",
            "severity": "HIGH",
            "published_at": datetime.now(timezone.utc),
            "server_id": 11,
            "match_confidence": 0.7,
        },
        {
            "advisory_id": 3,
            "ecosystem": "pypi",
            "severity": "CRITICAL",
            "published_at": datetime.now(timezone.utc),
            "server_id": 10,
            "match_confidence": 0.8,
        },
    ]
    _result = facet_ecosystem_facets(_rows, 30)
    assert len(_result["facets"]) == 2
    assert _result["totals"]["advisories"] == 3
    assert _result["totals"]["linked_servers"] == 2
    print("PASS")