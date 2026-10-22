from __future__ import annotations

from typing import Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


__all__ = [
    "get_match_basis_distribution",
    "get_vuln_match_basis_distribution",
    "get_match_basis_stats",
    "fetch_match_basis_distribution",
]


def _build_payload(
    basis_rows: list[dict[str, Any]],
    severity_rows: list[dict[str, Any]],
    top_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    by_basis = [
        {
            "basis": str(row["basis"]),
            "count": int(row["count"]),
            "avg_confidence": float(row["avg_confidence"] or 0.0),
            "distinct_servers": int(row["distinct_servers"]),
        }
        for row in sorted(basis_rows, key=lambda item: str(item["basis"]))
    ]

    by_severity = [
        {
            "severity": str(row["severity"]),
            "count": int(row["count"]),
        }
        for row in sorted(severity_rows, key=lambda item: str(item["severity"]))
    ]

    top_values: dict[str, list[str]] = {
        row["basis"]: [] for row in by_basis
    }
    top_value_counts: dict[str, list[dict[str, Any]]] = {
        row["basis"]: [] for row in by_basis
    }

    for row in top_rows:
        basis = str(row["basis"])
        match_value = str(row["match_value"])
        top_values.setdefault(basis, []).append(match_value)
        top_value_counts.setdefault(basis, []).append(
            {
                "match_value": match_value,
                "count": int(row["count"]),
            }
        )

    return {
        "total_links": sum(row["count"] for row in by_basis),
        "by_basis": by_basis,
        "by_severity": by_severity,
        "top_match_values": top_values,
        "top_match_value_counts": top_value_counts,
    }


def get_match_basis_distribution(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    basis_query = (
        select(
            VulnLink.match_basis.label("basis"),
            func.count(VulnLink.id).label("count"),
            func.coalesce(func.avg(VulnLink.match_confidence), 0.0).label(
                "avg_confidence"
            ),
            func.count(func.distinct(VulnLink.server_id)).label(
                "distinct_servers"
            ),
        )
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .group_by(VulnLink.match_basis)
    )

    severity_query = (
        select(
            VulnAdvisory.severity.label("severity"),
            func.count(VulnLink.id).label("count"),
        )
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .group_by(VulnAdvisory.severity)
    )

    ranked_values = (
        select(
            VulnLink.match_basis.label("basis"),
            VulnLink.match_value.label("match_value"),
            func.count(VulnLink.id).label("count"),
            func.row_number()
            .over(
                partition_by=VulnLink.match_basis,
                order_by=(
                    func.count(VulnLink.id).desc(),
                    VulnLink.match_value.asc(),
                ),
            )
            .label("value_rank"),
        )
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .group_by(VulnLink.match_basis, VulnLink.match_value)
        .subquery()
    )

    top_query = (
        select(
            ranked_values.c.basis,
            ranked_values.c.match_value,
            ranked_values.c.count,
        )
        .where(ranked_values.c.value_rank <= 10)
        .order_by(
            ranked_values.c.basis.asc(),
            ranked_values.c.value_rank.asc(),
        )
    )

    basis_rows = [
        {
            "basis": row.basis if row.basis is not None else "unknown",
            "count": int(row.count),
            "avg_confidence": float(row.avg_confidence or 0.0),
            "distinct_servers": int(row.distinct_servers),
        }
        for row in session.execute(basis_query).all()
    ]

    severity_rows = [
        {
            "severity": row.severity if row.severity is not None else "unknown",
            "count": int(row.count),
        }
        for row in session.execute(severity_query).all()
    ]

    top_rows = [
        {
            "basis": row.basis if row.basis is not None else "unknown",
            "match_value": (
                row.match_value if row.match_value is not None else "unknown"
            ),
            "count": int(row.count),
        }
        for row in session.execute(top_query).all()
    ]

    return _build_payload(basis_rows, severity_rows, top_rows)


get_vuln_match_basis_distribution = get_match_basis_distribution
get_match_basis_stats = get_match_basis_distribution
fetch_match_basis_distribution = get_match_basis_distribution


def _self_test() -> None:
    basis_rows = [
        {
            "basis": "cpe",
            "count": 4,
            "avg_confidence": 0.91,
            "distinct_servers": 3,
        },
        {
            "basis": "version",
            "count": 2,
            "avg_confidence": 0.73,
            "distinct_servers": 2,
        },
    ]
    severity_rows = [
        {"severity": "critical", "count": 2},
        {"severity": "high", "count": 3},
        {"severity": "medium", "count": 1},
    ]
    top_rows = [
        {"basis": "cpe", "match_value": "cpe:/a:vendor:product", "count": 2},
        {"basis": "cpe", "match_value": "cpe:/a:vendor:other", "count": 1},
        {"basis": "version", "match_value": "1.2.3", "count": 1},
        {"basis": "version", "match_value": "2.0.0", "count": 1},
    ]

    payload = _build_payload(basis_rows, severity_rows, top_rows)

    assert payload["total_links"] == 6
    assert sum(row["count"] for row in payload["by_basis"]) == payload[
        "total_links"
    ]
    assert next(
        row for row in payload["by_basis"] if row["basis"] == "cpe"
    )["count"] == 4
    assert any(
        row["severity"] == "critical" for row in payload["by_severity"]
    )
    assert len(payload["top_match_values"]["cpe"]) == 2
    assert len(payload["top_match_values"]["version"]) == 2

    print("PASS")


if __name__ == "__main__":
    _self_test()