# deps: fastapi, sqlalchemy, pydantic
"""Vuln Advisory Feed v2 -- paginated, filterable feed of vulnerability advisories
with server linkage counts and severity distribution."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Ensure repo root is on path so `from app.db` and `from app.models` resolve
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["vuln_advisory_feed_v2"])


class AdvisoryFeedItem(BaseModel):
    id: str
    feed: str
    summary: Optional[str] = None
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    source_url: str
    published_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    linked_server_count: int = 0
    max_confidence: float = 0.0


class AdvisoryFeedResponse(BaseModel):
    items: List[AdvisoryFeedItem]
    total: int
    limit: int
    offset: int


class ServerLinkage(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    match_basis: str
    match_value: str
    match_confidence: float


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
    linked_servers: List[ServerLinkage]


class ServerFeedItem(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    total_advisories: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int


class ServerFeedResponse(BaseModel):
    items: List[ServerFeedItem]
    total: int
    limit: int
    offset: int


@router.get("/vuln_advisory_feed_v2", response_model=AdvisoryFeedResponse)
def list_advisory_feed(
    feed: Optional[str] = Query(None, description="Filter by feed: nvd, ghsa, osv"),
    severity: Optional[str] = Query(None, description="Filter by severity: CRITICAL, HIGH, MEDIUM, LOW, UNKNOWN"),
    ecosystem: Optional[str] = Query(None, description="Filter by ecosystem: npm, PyPI, GitHub Actions, etc."),
    package: Optional[str] = Query(None, description="Filter by package name (partial match)"),
    has_links: Optional[bool] = Query(None, description="Only return advisories with linked servers"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
) -> AdvisoryFeedResponse:
    """Paginated, filterable vulnerability advisory feed with linkage metadata."""
    q = db.query(VulnAdvisory)
    if feed:
        q = q.filter(VulnAdvisory.feed == feed)
    if severity:
        q = q.filter(VulnAdvisory.severity == severity)
    if ecosystem:
        q = q.filter(VulnAdvisory.ecosystem == ecosystem)
    if package:
        q = q.filter(VulnAdvisory.package.ilike(f"%{package}%"))

    total = q.count()

    if has_links is True:
        q = q.filter(
            VulnAdvisory.id.in_(
                db.query(VulnLink.advisory_id).distinct()
            )
        )
        total = q.count()
    elif has_links is False:
        q = q.filter(
            ~VulnAdvisory.id.in_(
                db.query(VulnLink.advisory_id).distinct()
            )
        )
        total = q.count()

    advisories = (
        q.order_by(VulnAdvisory.fetched_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items: List[AdvisoryFeedItem] = []
    for adv in advisories:
        linkage = (
            db.query(
                func.count(VulnLink.server_id).label("cnt"),
                func.max(VulnLink.match_confidence).label("max_conf"),
            )
            .filter(VulnLink.advisory_id == adv.id)
            .first()
        )
        items.append(
            AdvisoryFeedItem(
                id=adv.id,
                feed=adv.feed,
                summary=adv.summary,
                severity=adv.severity,
                ecosystem=adv.ecosystem,
                package=adv.package,
                source_url=adv.source_url,
                published_at=adv.published_at,
                fetched_at=adv.fetched_at,
                linked_server_count=linkage.cnt if linkage else 0,
                max_confidence=float(linkage.max_conf) if linkage and linkage.max_conf else 0.0,
            )
        )

    return AdvisoryFeedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/vuln_advisory_feed_v2/{advisory_id}", response_model=AdvisoryDetailResponse)
def get_advisory_feed_detail(
    advisory_id: str,
    db: Session = Depends(get_session),
) -> AdvisoryDetailResponse:
    """Return full details for a single advisory, including all linked servers."""
    adv = db.query(VulnAdvisory).filter(VulnAdvisory.id == advisory_id).first()
    if not adv:
        raise HTTPException(status_code=404, detail=f"Advisory {advisory_id} not found")

    links = (
        db.query(VulnLink, McpServerRegistry)
        .outerjoin(McpServerRegistry, VulnLink.server_id == McpServerRegistry.server_id)
        .filter(VulnLink.advisory_id == advisory_id)
        .all()
    )

    linked_servers = [
        ServerLinkage(
            server_id=lnk.server_id,
            name=srv.name if srv else None,
            registry_source=srv.registry_source if srv else None,
            risk_tier=srv.risk_tier if srv else None,
            match_basis=lnk.match_basis,
            match_value=lnk.match_value,
            match_confidence=float(lnk.match_confidence),
        )
        for lnk, srv in links
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
    )


@router.get("/vuln_advisory_feed_v2/servers/affected", response_model=ServerFeedResponse)
def list_affected_servers(
    severity: Optional[str] = Query(None, description="Filter by minimum severity: CRITICAL, HIGH, MEDIUM"),
    feed: Optional[str] = Query(None, description="Filter by advisory feed: nvd, ghsa, osv"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
) -> ServerFeedResponse:
    """List servers with at least one linked vulnerability, with severity breakdown."""
    base_q = (
        db.query(VulnLink.server_id)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
    )
    if feed:
        base_q = base_q.filter(VulnAdvisory.feed == feed)
    if severity:
        sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}
        min_level = sev_order.get(severity.upper(), 4)
        base_q = base_q.filter(
            VulnAdvisory.severity.in_(
                [k for k, v in sev_order.items() if v <= min_level]
            )
        )

    server_ids_subq = base_q.with_entities(func.distinct(VulnLink.server_id)).subquery()

    total = db.query(func.count()).select_from(server_ids_subq).scalar() or 0

    paged_ids = (
        db.query(func.distinct(VulnLink.server_id))
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .filter(VulnLink.server_id.in_(db.query(server_ids_subq)))
        .group_by(VulnLink.server_id)
        .order_by(func.count(VulnLink.advisory_id).desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    paged_ids = [r[0] for r in paged_ids]

    items: List[ServerFeedItem] = []
    for sid in paged_ids:
        srv = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == sid).first()
        sev_counts: Dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for row in (
            db.query(VulnAdvisory.severity, func.count(VulnAdvisory.id))
            .join(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
            .filter(VulnLink.server_id == sid)
            .group_by(VulnAdvisory.severity)
            .all()
        ):
            sev_counts[row[0] or "UNKNOWN"] = row[1]

        total_adv = (
            db.query(func.count(func.distinct(VulnLink.advisory_id)))
            .filter(VulnLink.server_id == sid)
            .scalar()
            or 0
        )
        items.append(
            ServerFeedItem(
                server_id=sid,
                name=srv.name if srv else None,
                registry_source=srv.registry_source if srv else None,
                risk_tier=srv.risk_tier if srv else None,
                total_advisories=total_adv,
                critical_count=sev_counts.get("CRITICAL", 0),
                high_count=sev_counts.get("HIGH", 0),
                medium_count=sev_counts.get("MEDIUM", 0),
                low_count=sev_counts.get("LOW", 0),
            )
        )

    return ServerFeedResponse(items=items, total=total, limit=limit, offset=offset)


if __name__ == "__main__":
    # Self-test: LOCAL FastAPI() + dependency override (no app.db side effects)
    _eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_eng)
    _TS = sessionmaker(bind=_eng, autoflush=False, autocommit=False)

    with _TS() as db:
        db.execute(
            text(
                """
            INSERT INTO mcp_server_registry (server_id, name, registry_source, risk_tier, url)
            VALUES
                ('srv1','Test Server 1','github','HIGH','https://github.com/srv1'),
                ('srv2','Test Server 2','npm','MEDIUM','https://npmjs.com/srv2'),
                ('srv3','Clean Server','github','LOW','https://github.com/srv3');
            """
            )
        )
        db.execute(
            text(
                """
            INSERT INTO vuln_advisories (id, feed, summary, severity, ecosystem, package, source_url, published_at, fetched_at)
            VALUES
                ('CVE-2023-0001','nvd','Critical RCE','CRITICAL','npm','evil-pkg','https://nvd/1','2023-01-01','2023-01-02'),
                ('CVE-2023-0002','nvd','High XSS','HIGH','npm','xss-pkg','https://nvd/2','2023-01-02','2023-01-02'),
                ('CVE-2023-0003','ghsa','Medium DoS','MEDIUM','PyPI','dos-pkg','https://ghsa/3','2023-01-03','2023-01-03');
            """
            )
        )
        db.execute(
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
        db.commit()

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

    # Test list feed
    resp = _c.get("/api/vuln_advisory_feed_v2")
    assert resp.status_code == 200, f"Feed list failed: {resp.status_code} {resp.text}"
    payload = resp.json()
    assert payload["total"] == 3, f"Expected 3 advisories, got {payload['total']}"
    assert len(payload["items"]) == 3

    # Test severity filter
    resp = _c.get("/api/vuln_advisory_feed_v2?severity=CRITICAL")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    # Test feed filter
    resp = _c.get("/api/vuln_advisory_feed_v2?feed=ghsa")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    # Test has_links=true
    resp = _c.get("/api/vuln_advisory_feed_v2?has_links=true")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2

    # Test advisory detail
    resp = _c.get("/api/vuln_advisory_feed_v2/CVE-2023-0001")
    assert resp.status_code == 200, f"Detail failed: {resp.status_code} {resp.text}"
    detail = resp.json()
    assert detail["id"] == "CVE-2023-0001"
    assert detail["severity"] == "CRITICAL"
    assert len(detail["linked_servers"]) == 2

    # Test 404
    resp = _c.get("/api/vuln_advisory_feed_v2/NOTFOUND")
    assert resp.status_code == 404

    # Test affected servers
    resp = _c.get("/api/vuln_advisory_feed_v2/servers/affected")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 2, f"Expected 2 affected servers, got {payload['total']}"
    assert payload["items"][0]["server_id"] == "srv1"
    assert payload["items"][0]["critical_count"] == 1
    assert payload["items"][0]["total_advisories"] == 2

    print("PASS")
