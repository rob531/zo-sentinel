"""Logic for vulnerability advisory link coverage summaries."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


def compute_coverage_summary(
    db: Session, _severity: Optional[str] = None
) -> Dict[str, Any]:
    """Summarize advisory coverage using the real application tables."""
    total_links = int(
        db.execute(select(func.count()).select_from(VulnLink)).scalar() or 0
    )
    distinct_servers = int(
        db.execute(
            select(func.count(func.distinct(VulnLink.server_id))).select_from(VulnLink)
        ).scalar()
        or 0
    )
    distinct_advisories = int(
        db.execute(
            select(func.count(func.distinct(VulnLink.advisory_id)))
            .select_from(VulnLink)
            .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
        ).scalar()
        or 0
    )

    severity_rows = db.execute(
        select(VulnAdvisory.severity, func.count(VulnLink.id))
        .select_from(VulnLink)
        .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
        .group_by(VulnAdvisory.severity)
    ).all()
    by_severity: Dict[str, int] = {}
    for severity, count in severity_rows:
        by_severity[severity or "UNKNOWN"] = int(count)

    basis_rows = db.execute(
        select(VulnLink.match_basis, func.count(VulnLink.id))
        .select_from(VulnLink)
        .group_by(VulnLink.match_basis)
    ).all()
    by_match_basis: Dict[str, int] = {}
    for basis, count in basis_rows:
        by_match_basis[basis or "unknown"] = int(count)

    mean_confidence = db.execute(
        select(func.avg(VulnLink.match_confidence)).select_from(VulnLink)
    ).scalar()

    unlinked_rows = db.execute(
        select(McpServerRegistry.server_id)
        .select_from(McpServerRegistry)
        .outerjoin(VulnLink, VulnLink.server_id == McpServerRegistry.server_id)
        .where(VulnLink.id.is_(None))
        .group_by(McpServerRegistry.server_id)
    ).all()

    return {
        "total_links": total_links,
        "distinct_servers": distinct_servers,
        "distinct_advisories": distinct_advisories,
        "by_match_basis": by_match_basis,
        "by_severity": by_severity,
        "mean_match_confidence": float(mean_confidence or 0.0),
        "unlinked_servers": len(unlinked_rows),
    }


def vuln_link_coverage_summary(
    db: Session, severity: Optional[str] = None
) -> Dict[str, Any]:
    """Return the vulnerability link coverage summary."""
    return compute_coverage_summary(db, severity)


get_vuln_link_coverage_summary = vuln_link_coverage_summary
get_coverage_summary = vuln_link_coverage_summary
coverage_summary = vuln_link_coverage_summary


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine, checkfirst=True)
    VulnAdvisory.__table__.create(engine, checkfirst=True)
    VulnLink.__table__.create(engine, checkfirst=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="server-a", name="Server A"),
                McpServerRegistry(server_id="server-b", name="Server B"),
                McpServerRegistry(server_id="server-c", name="Server C"),
                VulnAdvisory(
                    id="advisory-critical",
                    feed="contract",
                    source_url="https://example.test/advisory-critical",
                    severity="CRITICAL",
                    summary="Critical advisory",
                ),
                VulnAdvisory(
                    id="advisory-medium",
                    feed="contract",
                    source_url="https://example.test/advisory-medium",
                    severity="MEDIUM",
                    summary="Medium advisory",
                ),
                VulnLink(
                    id=1,
                    advisory_id="advisory-critical",
                    server_id="server-a",
                    match_basis="package",
                    match_value="example-package",
                    match_confidence=0.95,
                ),
                VulnLink(
                    id=2,
                    advisory_id="advisory-critical",
                    server_id="server-b",
                    match_basis="package",
                    match_value="example-package",
                    match_confidence=0.90,
                ),
                VulnLink(
                    id=3,
                    advisory_id="advisory-medium",
                    server_id="server-a",
                    match_basis="cve",
                    match_value="CVE-TEST-0001",
                    match_confidence=0.85,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/vuln/links/coverage")
    def coverage(severity: str = "SUMMARY", db: Session = Depends(get_session)):
        return vuln_link_coverage_summary(db, severity)

    response = TestClient(that_app).get(
        "/api/vuln/links/coverage", params={"severity": "SUMMARY"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_links"] == 3
    assert payload["by_severity"]["CRITICAL"] == 2
    assert payload["unlinked_servers"] == 1
    assert len(payload["by_match_basis"]) == 2
    print("PASS")