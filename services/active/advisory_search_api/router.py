# deps: fastapi, sqlalchemy, pydantic
"""Advisory Search API -- full-text and facet search over vulnerability advisories.

GET /api/advisories/search
  Search/filter VulnAdvisory rows by q (free-text), severity, ecosystem, feed,
  CVE ID (via aliases), and server_id (via VulnLink join).
  Returns affected_server_count per advisory.

Public endpoint -- no auth required.
Data: app Postgres via get_session + VulnAdvisory + VulnLink.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, or_, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["advisory_search_api"])


# --------------------------------------------------------------------------- #
# Pydantic request / response models
# --------------------------------------------------------------------------- #

class LinkedServerSummary(BaseModel):
    server_id: str
    match_basis: str
    match_value: str
    match_confidence: float

    model_config = {"from_attributes": True}


class AdvisoryItem(BaseModel):
    id: str
    feed: str
    summary: Optional[str] = None
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    affected_ranges: Optional[dict] = None
    aliases: Optional[dict] = None
    source_url: str
    published_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    linked_server_count: int = 0
    max_confidence: float = 0.0
    linked_servers: List[LinkedServerSummary] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class SearchResponse(BaseModel):
    items: List[AdvisoryItem]
    total: int
    limit: int
    offset: int


class AdvisoryDetailResponse(BaseModel):
    id: str
    feed: str
    summary: Optional[str] = None
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    affected_ranges: Optional[dict] = None
    aliases: Optional[dict] = None
    identities: Optional[dict] = None
    source_url: str
    published_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    linked_servers: List[LinkedServerSummary]
    linked_server_count: int
    max_confidence: float


class AffectedServersResponse(BaseModel):
    items: List[LinkedServerSummary]
    total: int


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/advisories/search", response_model=SearchResponse)
def search_advisories(
    q: Optional[str] = Query(None, description="Free-text search over summary and package"),
    cve_id: Optional[str] = Query(None, description="Filter by CVE/GHSA ID (matched in aliases)"),
    server_id: Optional[str] = Query(None, description="Filter to advisories linked to a specific server"),
    severity: Optional[str] = Query(None, description="Filter by severity: CRITICAL, HIGH, MEDIUM, LOW, UNKNOWN"),
    ecosystem: Optional[str] = Query(None, description="Filter by ecosystem: npm, PyPI, GitHub, etc."),
    feed: Optional[str] = Query(None, description="Filter by feed: nvd, ghsa, osv"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
) -> SearchResponse:
    """Search and filter vulnerability advisories with optional facets."""
    # Build base query on VulnAdvisory
    q_base = db.query(VulnAdvisory)

    # Free-text filter on summary and package
    if q:
        pattern = f"%{q}%"
        q_base = q_base.filter(
            or_(
                VulnAdvisory.summary.ilike(pattern),
                VulnAdvisory.package.ilike(pattern),
            )
        )

    # CVE/GHSA ID filter via aliases (JSON array column)
    if cve_id:
        q_base = q_base.filter(VulnAdvisory.aliases.ilike(f"%{cve_id}%"))

    # Severity filter
    if severity:
        q_base = q_base.filter(VulnAdvisory.severity == severity.upper())

    # Ecosystem filter
    if ecosystem:
        q_base = q_base.filter(VulnAdvisory.ecosystem == ecosystem)

    # Feed filter
    if feed:
        q_base = q_base.filter(VulnAdvisory.feed == feed)

    # Server filter: inner-join through VulnLink
    if server_id:
        q_base = q_base.join(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        q_base = q_base.filter(VulnLink.server_id == server_id)

    # Total count (before pagination)
    total = q_base.distinct().count()

    # Paginated results ordered by fetched_at desc
    rows = (
        q_base.distinct()
        .order_by(VulnAdvisory.fetched_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # For each advisory, compute linkage counts
    items: List[AdvisoryItem] = []
    advisory_ids = [r.id for r in rows]

    if advisory_ids:
        linkage_stats = (
            db.query(
                VulnLink.advisory_id,
                func.count(VulnLink.id).label("cnt"),
                func.max(VulnLink.match_confidence).label("max_conf"),
            )
            .filter(VulnLink.advisory_id.in_(advisory_ids))
            .group_by(VulnLink.advisory_id)
        )
        stat_map = {stat.advisory_id: stat for stat in linkage_stats}

        for adv in rows:
            stat = stat_map.get(adv.id)
            items.append(
                AdvisoryItem(
                    id=adv.id,
                    feed=adv.feed,
                    summary=adv.summary,
                    severity=adv.severity,
                    ecosystem=adv.ecosystem,
                    package=adv.package,
                    affected_ranges=adv.affected_ranges,
                    aliases=adv.aliases,
                    source_url=adv.source_url,
                    published_at=adv.published_at,
                    fetched_at=adv.fetched_at,
                    linked_server_count=int(stat.cnt) if stat else 0,
                    max_confidence=float(stat.max_conf) if stat and stat.max_conf else 0.0,
                    linked_servers=[],
                )
            )
    else:
        for adv in rows:
            items.append(
                AdvisoryItem(
                    id=adv.id,
                    feed=adv.feed,
                    summary=adv.summary,
                    severity=adv.severity,
                    ecosystem=adv.ecosystem,
                    package=adv.package,
                    affected_ranges=adv.affected_ranges,
                    aliases=adv.aliases,
                    source_url=adv.source_url,
                    published_at=adv.published_at,
                    fetched_at=adv.fetched_at,
                    linked_server_count=0,
                    max_confidence=0.0,
                    linked_servers=[],
                )
            )

    return SearchResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/advisories/{advisory_id}", response_model=AdvisoryDetailResponse)
def get_advisory(
    advisory_id: str,
    db: Session = Depends(get_session),
) -> AdvisoryDetailResponse:
    """Return full details for a single advisory, including all linked servers."""
    adv = db.query(VulnAdvisory).filter(VulnAdvisory.id == advisory_id).first()
    if not adv:
        raise HTTPException(status_code=404, detail=f"Advisory {advisory_id} not found")

    links = (
        db.query(VulnLink)
        .filter(VulnLink.advisory_id == advisory_id)
        .order_by(VulnLink.match_confidence.desc())
        .all()
    )

    linked_servers = [
        LinkedServerSummary(
            server_id=lnk.server_id,
            match_basis=lnk.match_basis,
            match_value=lnk.match_value,
            match_confidence=float(lnk.match_confidence),
        )
        for lnk in links
    ]

    return AdvisoryDetailResponse(
        id=adv.id,
        feed=adv.feed,
        summary=adv.summary,
        severity=adv.severity,
        ecosystem=adv.ecosystem,
        package=adv.package,
        affected_ranges=adv.affected_ranges,
        aliases=adv.aliases,
        identities=adv.identities,
        source_url=adv.source_url,
        published_at=adv.published_at,
        fetched_at=adv.fetched_at,
        linked_servers=linked_servers,
        linked_server_count=len(links),
        max_confidence=float(links[0].match_confidence) if links else 0.0,
    )


@router.get("/advisories/{advisory_id}/affected-servers", response_model=AffectedServersResponse)
def get_affected_servers(
    advisory_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
) -> AffectedServersResponse:
    """Return all servers linked to a given advisory."""
    adv = db.query(VulnAdvisory).filter(VulnAdvisory.id == advisory_id).first()
    if not adv:
        raise HTTPException(status_code=404, detail=f"Advisory {advisory_id} not found")

    total = (
        db.query(func.count(VulnLink.id))
        .filter(VulnLink.advisory_id == advisory_id)
        .scalar()
        or 0
    )

    links = (
        db.query(VulnLink)
        .filter(VulnLink.advisory_id == advisory_id)
        .order_by(VulnLink.match_confidence.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = [
        LinkedServerSummary(
            server_id=lnk.server_id,
            match_basis=lnk.match_basis,
            match_value=lnk.match_value,
            match_confidence=float(lnk.match_confidence),
        )
        for lnk in links
    ]

    return AffectedServersResponse(items=items, total=int(total))


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    _eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_eng)
    _TS = sessionmaker(bind=_eng, autoflush=False, autocommit=False)

    with _TS() as _sess:
        _sess.execute(
            text(
                """
            INSERT INTO vuln_advisories (id, feed, summary, severity, ecosystem, package, source_url, published_at, fetched_at, aliases, affected_ranges, identities)
            VALUES
                ('CVE-2023-0001','nvd','Critical RCE in npm package','CRITICAL','npm','evil-pkg','https://nvd/1','2023-01-01','2023-01-02','["CVE-2023-0001"]','[]','{}'),
                ('CVE-2023-0002','ghsa','High XSS in pypi package','HIGH','PyPI','xss-pkg','https://ghsa/2','2023-01-02','2023-01-02','["CVE-2023-0002"]','[]','{}'),
                ('CVE-2023-0003','nvd','Medium DoS in npm module','MEDIUM','npm','dos-pkg','https://nvd/3','2023-01-03','2023-01-03','["CVE-2023-0003"]','[]','{}'),
                ('CVE-2023-0004','ghsa','Low info disclosure','LOW','GitHub','info-pkg','https://ghsa/4','2023-01-04','2023-01-04','["CVE-2023-0004"]','[]','{}');
            """
            )
        )
        _sess.execute(
            text(
                """
            INSERT INTO vuln_links (advisory_id, server_id, match_basis, match_value, match_confidence)
            VALUES
                ('CVE-2023-0001','srv1','package_exact','evil-pkg',1.0),
                ('CVE-2023-0001','srv2','package_exact','evil-pkg',0.95),
                ('CVE-2023-0002','srv1','package_exact','xss-pkg',0.90);
            """
            )
        )
        _sess.commit()

    _that_app = FastAPI()
    _that_app.include_router(router)

    def _override_session():
        s = _TS()
        try:
            yield s
        finally:
            s.close()

    _that_app.dependency_overrides[get_session] = _override_session
    _c = TestClient(_that_app)

    # Test 1: search with no filters returns all 4
    resp = _c.get("/api/advisories/search")
    assert resp.status_code == 200, f"search failed: {resp.status_code} {resp.text}"
    payload = resp.json()
    assert payload["total"] == 4, f"Expected 4 advisories, got {payload['total']}"
    assert len(payload["items"]) == 4

    # Test 2: severity filter
    resp = _c.get("/api/advisories/search?severity=CRITICAL")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    # Test 3: ecosystem filter
    resp = _c.get("/api/advisories/search?ecosystem=npm")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2

    # Test 4: feed filter
    resp = _c.get("/api/advisories/search?feed=ghsa")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2

    # Test 5: free-text search
    resp = _c.get("/api/advisories/search?q=RCE")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["id"] == "CVE-2023-0001"

    # Test 6: CVE ID filter
    resp = _c.get("/api/advisories/search?cve_id=CVE-2023-0002")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    # Test 7: server_id filter (only CVE-2023-0001 and CVE-2023-0002 are linked)
    resp = _c.get("/api/advisories/search?server_id=srv1")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2

    # Test 8: detail endpoint
    resp = _c.get("/api/advisories/CVE-2023-0001")
    assert resp.status_code == 200, f"detail failed: {resp.status_code} {resp.text}"
    detail = resp.json()
    assert detail["id"] == "CVE-2023-0001"
    assert detail["linked_server_count"] == 2
    assert detail["max_confidence"] == 1.0
    assert len(detail["linked_servers"]) == 2

    # Test 9: detail 404
    resp = _c.get("/api/advisories/NOTFOUND")
    assert resp.status_code == 404

    # Test 10: affected servers
    resp = _c.get("/api/advisories/CVE-2023-0001/affected-servers")
    assert resp.status_code == 200
    aff = resp.json()
    assert aff["total"] == 2
    assert aff["items"][0]["server_id"] == "srv1"
    assert aff["items"][0]["match_confidence"] == 1.0

    # Test 11: pagination
    resp = _c.get("/api/advisories/search?limit=2&offset=0")
    assert resp.status_code == 200
    assert resp.json()["total"] == 4
    assert len(resp.json()["items"]) == 2
    resp2 = _c.get("/api/advisories/search?limit=2&offset=2")
    assert resp2.json()["total"] == 4
    assert len(resp2.json()["items"]) == 2

    print("PASS")
