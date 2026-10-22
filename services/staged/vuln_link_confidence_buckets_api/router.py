from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

try:
    from .logic import bucket_links as _logic_bucket_links
except ImportError:  # pragma: no cover - supports direct execution of this module
    try:
        from logic import bucket_links as _logic_bucket_links
    except ImportError:
        _logic_bucket_links = None


router = APIRouter(prefix="/api", tags=["vulnerability-links"])


class ConfidenceBucket(BaseModel):
    bucket: str
    range: list[float]
    count: int
    share: float


class BasisSummary(BaseModel):
    match_basis: str | None = None
    count: int
    avg_confidence: float | None = None


class SeveritySummary(BaseModel):
    severity: str | None = None
    count: int


class ConfidenceResponse(BaseModel):
    days: int
    buckets: list[ConfidenceBucket]
    by_basis: list[BasisSummary]
    by_severity: list[SeveritySummary]
    total_links: int
    missing_confidence: int


_BUCKET_DEFINITIONS = (
    ("high", [0.8, 1.0]),
    ("medium", [0.5, 0.8]),
    ("low", [0.0, 0.5]),
)


def _confidence_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def bucket_links(rows: list[dict]) -> dict:
    """Build the confidence profile from already-fetched vulnerability-link rows."""
    buckets = [
        {"bucket": name, "range": list(bounds), "count": 0, "share": 0.0}
        for name, bounds in _BUCKET_DEFINITIONS
    ]
    by_basis_values: dict[str, dict[str, Any]] = {}
    by_severity_values: dict[str, int] = {}
    total_links = len(rows)
    missing_confidence = 0

    for row in rows:
        confidence = _confidence_value(row.get("match_confidence"))
        if confidence is None:
            missing_confidence += 1
            bucket_name = "low"
        elif confidence >= 0.8:
            bucket_name = "high"
        elif confidence >= 0.5:
            bucket_name = "medium"
        else:
            bucket_name = "low"

        for bucket in buckets:
            if bucket["bucket"] == bucket_name:
                bucket["count"] += 1
                break

        basis = row.get("match_basis")
        basis_key = str(basis) if basis is not None else ""
        basis_summary = by_basis_values.setdefault(
            basis_key,
            {"match_basis": basis, "count": 0, "confidence_sum": 0.0, "confidence_count": 0},
        )
        basis_summary["count"] += 1
        if confidence is not None:
            basis_summary["confidence_sum"] += confidence
            basis_summary["confidence_count"] += 1

        severity = row.get("severity")
        severity_key = str(severity) if severity is not None else ""
        by_severity_values[severity_key] = by_severity_values.get(severity_key, 0) + 1

    for bucket in buckets:
        bucket["share"] = round(bucket["count"] / total_links, 4) if total_links else 0.0

    by_basis = []
    for basis_summary in by_basis_values.values():
        confidence_count = basis_summary["confidence_count"]
        avg_confidence = (
            round(basis_summary["confidence_sum"] / confidence_count, 4)
            if confidence_count
            else None
        )
        by_basis.append(
            {
                "match_basis": basis_summary["match_basis"],
                "count": basis_summary["count"],
                "avg_confidence": avg_confidence,
            }
        )

    by_basis.sort(key=lambda item: (item["match_basis"] is None, item["match_basis"] or ""))
    by_severity = [
        {"severity": severity, "count": count}
        for severity, count in sorted(
            by_severity_values.items(),
            key=lambda item: (item[0] != "", item[0]),
        )
    ]

    return {
        "buckets": buckets,
        "by_basis": by_basis,
        "by_severity": by_severity,
        "total_links": total_links,
        "missing_confidence": missing_confidence,
    }


@router.get(
    "/vuln/links/confidence",
    response_model=ConfidenceResponse,
    summary="Confidence profile for CVE-to-MCP-server links",
)
def get_vuln_link_confidence(
    days: int = Query(default=90, ge=1, le=365),
    session=Depends(get_session),
) -> dict:
    cutoff = datetime.utcnow() - timedelta(days=days)
    statement = (
        select(VulnLink, VulnAdvisory)
        .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
        .where(VulnLink.linked_at >= cutoff)
    )
    result = session.execute(statement)
    rows = []

    for link, advisory in result.all():
        rows.append(
            {
                "id": link.id,
                "advisory_id": link.advisory_id,
                "server_id": link.server_id,
                "match_basis": link.match_basis,
                "match_value": link.match_value,
                "match_confidence": link.match_confidence,
                "linked_at": link.linked_at,
                "severity": advisory.severity,
                "ecosystem": advisory.ecosystem,
                "package": advisory.package,
                "published_at": advisory.published_at,
            }
        )

    profile = bucket_links(rows)
    return {"days": days, **profile}


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    class FakeLink:
        def __init__(self, link_id: int, confidence: float | None, basis: str) -> None:
            self.id = link_id
            self.advisory_id = link_id
            self.server_id = link_id
            self.match_basis = basis
            self.match_value = "demo"
            self.match_confidence = confidence
            self.linked_at = datetime.utcnow()

    class FakeAdvisory:
        def __init__(self, advisory_id: int, severity: str) -> None:
            self.id = advisory_id
            self.severity = severity
            self.ecosystem = "npm"
            self.package = "demo-package"
            self.published_at = datetime.utcnow()

    class FakeResult:
        def __init__(self, rows: list[tuple[FakeLink, FakeAdvisory]]) -> None:
            self._rows = rows

        def all(self) -> list[tuple[FakeLink, FakeAdvisory]]:
            return self._rows

    class FakeSession:
        def __init__(self) -> None:
            self.rows = [
                (FakeLink(1, 0.9, "package_name"), FakeAdvisory(1, "HIGH")),
                (FakeLink(2, 0.6, "cve_id"), FakeAdvisory(2, "MEDIUM")),
                (FakeLink(3, 0.2, "package_name"), FakeAdvisory(3, "LOW")),
            ]

        def execute(self, statement: Any) -> FakeResult:
            return FakeResult(self.rows)

    test_app = FastAPI()
    test_app.include_router(router)
    fake_session = FakeSession()
    test_app.dependency_overrides[get_session] = lambda: fake_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/links/confidence?days=90")
        assert response.status_code == 200
        payload = response.json()
        assert "buckets" in payload
        assert "by_basis" in payload
        assert "by_severity" in payload
        assert "total_links" in payload
        assert payload["total_links"] == 3
        buckets = {item["bucket"]: item for item in payload["buckets"]}
        assert buckets["high"]["count"] == 1
        assert buckets["low"]["count"] == 1

    print("PASS")


if __name__ == "__main__":
    _run_self_test()