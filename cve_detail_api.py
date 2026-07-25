"""cve_detail_api.py -- CVE/advisory detail endpoint.

Returns full advisory details from vuln_advisories and associated vuln_links
for a given advisory_id.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["cve"])


class AdvisoryDetail(BaseModel):
    id: str
    feed: Optional[str] = None
    summary: Optional[str] = None
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    affected_ranges: Optional[dict] = None
    aliases: Optional[dict] = None
    source_url: Optional[str] = None
    published_at: Optional[datetime] = None


class VulnLinkDetail(BaseModel):
    server_id: str
    match_basis: Optional[str] = None
    match_confidence: Optional[float] = None
    linked_at: Optional[datetime] = None


class CveDetailResponse(BaseModel):
    advisory: AdvisoryDetail
    links: List[VulnLinkDetail]


@router.get("/cve/{advisory_id}", response_model=CveDetailResponse)
def get_cve_detail(advisory_id: str, db: Session = Depends(get_session)) -> CveDetailResponse:
    """Return full advisory details and associated vuln_links for an advisory_id."""
    advisory = db.get(VulnAdvisory, advisory_id)
    if advisory is None:
        raise HTTPException(status_code=404, detail=f"Advisory {advisory_id!r} not found")

    link_rows = db.execute(
        select(VulnLink).where(VulnLink.advisory_id == advisory_id)
    ).scalars().all()

    advisory_out = AdvisoryDetail(
        id=advisory.id,
        feed=advisory.feed,
        summary=advisory.summary,
        severity=advisory.severity,
        ecosystem=advisory.ecosystem,
        package=advisory.package,
        affected_ranges=advisory.affected_ranges,
        aliases=advisory.aliases,
        source_url=advisory.source_url,
        published_at=advisory.published_at,
    )

    links_out = [
        VulnLinkDetail(
            server_id=lnk.server_id,
            match_basis=lnk.match_basis,
            match_confidence=lnk.match_confidence,
            linked_at=lnk.linked_at,
        )
        for lnk in link_rows
    ]

    return CveDetailResponse(advisory=advisory_out, links=links_out)


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
    s = TS()
    s.add(VulnAdvisory(
        id="CVE-2024-0001",
        feed="nvd",
        summary="Test vulnerability",
        severity="HIGH",
        ecosystem="npm",
        package="test-pkg",
        affected_ranges={"ranges": []},
        aliases={"aliases": ["GHSA-2024-0001"]},
        source_url="https://nvd.nist.gov/vuln/detail/CVE-2024-0001",
        published_at=datetime(2024, 1, 1, 0, 0, 0),
    ))
    s.add(VulnLink(
        advisory_id="CVE-2024-0001",
        server_id="srv123",
        match_basis="package_exact",
        match_value="test-pkg",
        match_confidence=1.0,
    ))
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

    r = c.get("/api/cve/CVE-2024-0001")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["advisory"]["id"] == "CVE-2024-0001", j
    assert j["advisory"]["severity"] == "HIGH", j
    assert len(j["links"]) == 1, j
    assert j["links"][0]["server_id"] == "srv123", j
    assert j["links"][0]["match_confidence"] == 1.0, j

    assert c.get("/api/cve/NOTFOUND").status_code == 404

    print("PASS")
