"""CVE severity summary per MCP server.

Returns the CVE severity distribution (critical/high/medium/low) for a given
server_id by joining vuln_advisories -> vuln_links.
"""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["cve"])


class CveCounts(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class CveSummary(BaseModel):
    server_id: str
    cve_counts: CveCounts
    total: int


def _severity_key(sev: Optional[str]) -> str:
    """Normalise advisory severity to one of the four canonical buckets."""
    s = (sev or "").strip().upper()
    if s == "CRITICAL":
        return "critical"
    if s == "HIGH":
        return "high"
    if s == "MEDIUM":
        return "medium"
    if s == "LOW":
        return "low"
    return ""


@router.get("/cve-summary/{server_id}", response_model=CveSummary)
def get_cve_summary(server_id: str, db: Session = Depends(get_session)) -> CveSummary:
    """Return CVE severity counts for a server identified by server_id.

    advisory_ids are collected from vuln_links where server_id matches,
    then severity is read from the joined vuln_advisories rows.
    """
    advisory_ids = db.execute(
        select(VulnLink.advisory_id).where(VulnLink.server_id == server_id)
    ).scalars().all()

    if not advisory_ids:
        return CveSummary(
            server_id=server_id,
            cve_counts=CveCounts(),
            total=0,
        )

    rows = db.execute(
        select(VulnAdvisory.severity)
        .where(VulnAdvisory.id.in_(advisory_ids))
    ).scalars().all()

    counts = CveCounts()
    for sev in rows:
        bucket = _severity_key(sev)
        if bucket:
            setattr(counts, bucket, getattr(counts, bucket) + 1)

    return CveSummary(
        server_id=server_id,
        cve_counts=counts,
        total=len(rows),
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # Seed: srv1 has 3 advisories (CRITICAL, HIGH, MEDIUM), srv2 has 2 (LOW, LOW)
    s = TS()
    s.add(VulnAdvisory(id="CVE-2025-0001", feed="nvd", severity="CRITICAL",
                       summary="critical vuln", source_url="https://nvd.nist.gov/cve/2025-0001"))
    s.add(VulnAdvisory(id="CVE-2025-0002", feed="nvd", severity="HIGH",
                       summary="high vuln", source_url="https://nvd.nist.gov/cve/2025-0002"))
    s.add(VulnAdvisory(id="CVE-2025-0003", feed="osv", severity="MEDIUM",
                       summary="medium vuln", source_url="https://osv.dev/vuln/2025-0003"))
    s.add(VulnAdvisory(id="CVE-2025-0004", feed="osv", severity="LOW",
                       summary="low vuln", source_url="https://osv.dev/vuln/2025-0004"))
    s.add(VulnAdvisory(id="CVE-2025-0005", feed="osv", severity="LOW",
                       summary="low vuln 2", source_url="https://osv.dev/vuln/2025-0005"))
    s.add(VulnLink(advisory_id="CVE-2025-0001", server_id="srv1",
                   match_basis="repo_exact", match_value="test/repo", match_confidence=1.0))
    s.add(VulnLink(advisory_id="CVE-2025-0002", server_id="srv1",
                   match_basis="repo_exact", match_value="test/repo", match_confidence=1.0))
    s.add(VulnLink(advisory_id="CVE-2025-0003", server_id="srv1",
                   match_basis="repo_exact", match_value="test/repo", match_confidence=1.0))
    s.add(VulnLink(advisory_id="CVE-2025-0004", server_id="srv2",
                   match_basis="repo_exact", match_value="test/repo2", match_confidence=1.0))
    s.add(VulnLink(advisory_id="CVE-2025-0005", server_id="srv2",
                   match_basis="repo_exact", match_value="test/repo2", match_confidence=1.0))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    # srv1: 1 CRITICAL, 1 HIGH, 1 MEDIUM
    r = c.get("/api/cve-summary/srv1")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["server_id"] == "srv1", j
    assert j["cve_counts"]["critical"] == 1, j
    assert j["cve_counts"]["high"] == 1, j
    assert j["cve_counts"]["medium"] == 1, j
    assert j["cve_counts"]["low"] == 0, j
    assert j["total"] == 3, j

    # srv2: 2 LOW
    r2 = c.get("/api/cve-summary/srv2")
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["cve_counts"]["low"] == 2, j2
    assert j2["total"] == 2, j2

    # srv_unknown: no advisories -> zero counts
    r3 = c.get("/api/cve-summary/srv_unknown")
    assert r3.status_code == 200, r3.text
    j3 = r3.json()
    assert j3["cve_counts"]["critical"] == 0, j3
    assert j3["total"] == 0, j3

    print("PASS")
