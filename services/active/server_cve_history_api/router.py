# deps: fastapi, pydantic, sqlalchemy
"""Server CVE History API.

Returns CVE history and severity timeline for MCP servers.
Data: app Postgres via SQLAlchemy Session (from app.db import get_session).

Endpoints:
  GET /api/servers/{server_id}/cve-history       -- full CVE history for a server
  GET /api/servers/{server_id}/cve-history/{cve_id} -- single CVE detail for a server
  GET /api/servers/cve-history                     -- servers with CVE history (paginated)
  GET /api/cve-history/summary                     -- global CVE history summary
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

try:
    from app.db import get_session
    from app.models import McpServerRegistry, VulnAdvisory, VulnLink, Base
except ImportError:
    get_session = None  # type: ignore[assignment]
    McpServerRegistry = None  # type: ignore[assignment]
    VulnAdvisory = None  # type: ignore[assignment]
    VulnLink = None  # type: ignore[assignment]
    Base = None  # type: ignore[assignment]

router = APIRouter(prefix="/api", tags=["server_cve_history_api"])


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class CVESeverityCounts(BaseModel):
    CRITICAL: int = 0
    HIGH: int = 0
    MEDIUM: int = 0
    LOW: int = 0
    UNKNOWN: int = 0


class LinkedAdvisory(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    summary: Optional[str] = None
    severity: Optional[str] = None
    feed: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    source_url: Optional[str] = None
    published_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    match_basis: Optional[str] = None
    match_value: Optional[str] = None
    match_confidence: Optional[float] = None
    linked_at: Optional[datetime] = None


class ServerCVEHistoryResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    total_cves: int
    by_severity: CVESeverityCounts
    advisories: List[LinkedAdvisory]


class ServerCVEHistoryDetailResponse(BaseModel):
    server_id: str
    cve_id: str
    advisory: Optional[LinkedAdvisory] = None
    found: bool


class ServerCVEListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    total_cves: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int


class ServerCVEHistoryListResponse(BaseModel):
    items: List[ServerCVEListItem]
    total: int
    skip: int
    limit: int


class CVEHistorySummary(BaseModel):
    total_servers: int
    servers_with_cves: int
    total_advisories: int
    total_links: int
    by_severity: CVESeverityCounts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _severity_counts(sev_list: List[Optional[str]]) -> CVESeverityCounts:
    counts = CVESeverityCounts()
    for s in sev_list:
        if s == "CRITICAL":
            counts.CRITICAL += 1
        elif s == "HIGH":
            counts.HIGH += 1
        elif s == "MEDIUM":
            counts.MEDIUM += 1
        elif s == "LOW":
            counts.LOW += 1
        else:
            counts.UNKNOWN += 1
    return counts


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/servers/{server_id}/cve-history",
    response_model=ServerCVEHistoryResponse,
    name="server_cve_history:get",
)
def get_server_cve_history(
    server_id: str,
    days: int = Query(default=365, ge=1, le=3650, description="Window in days"),
    db: Session = Depends(get_session),
) -> ServerCVEHistoryResponse:
    """Return full CVE history for a server within the given time window."""
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    cutoff = datetime.utcnow() - __import__("datetime").timedelta(days=days)

    rows = (
        db.query(VulnLink, VulnAdvisory)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .filter(
            VulnLink.server_id == server_id,
            VulnAdvisory.published_at >= cutoff,
        )
        .order_by(VulnAdvisory.published_at.desc().nullslast())
        .all()
    )

    sevs: List[Optional[str]] = []
    advisories: List[LinkedAdvisory] = []
    for link, adv in rows:
        sevs.append(adv.severity)
        advisories.append(
            LinkedAdvisory(
                id=adv.id,
                summary=adv.summary,
                severity=adv.severity,
                feed=adv.feed,
                ecosystem=adv.ecosystem,
                package=adv.package,
                source_url=adv.source_url,
                published_at=adv.published_at,
                fetched_at=adv.fetched_at,
                match_basis=link.match_basis,
                match_value=link.match_value,
                match_confidence=float(link.match_confidence) if link.match_confidence else None,
                linked_at=link.linked_at,
            )
        )

    return ServerCVEHistoryResponse(
        server_id=server_id,
        name=server.name,
        registry_source=server.registry_source,
        risk_tier=server.risk_tier,
        total_cves=len(advisories),
        by_severity=_severity_counts(sevs),
        advisories=advisories,
    )


@router.get(
    "/servers/{server_id}/cve-history/{cve_id}",
    response_model=ServerCVEHistoryDetailResponse,
    name="server_cve_history:get_one",
)
def get_server_cve_detail(
    server_id: str,
    cve_id: str,
    db: Session = Depends(get_session),
) -> ServerCVEHistoryDetailResponse:
    """Return a single CVE record for a specific server."""
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    row = (
        db.query(VulnLink, VulnAdvisory)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .filter(
            VulnLink.server_id == server_id,
            VulnAdvisory.id == cve_id,
        )
        .first()
    )

    if not row:
        raise HTTPException(status_code=404, detail="CVE not found for this server")

    link, adv = row
    return ServerCVEHistoryDetailResponse(
        server_id=server_id,
        cve_id=cve_id,
        advisory=LinkedAdvisory(
            id=adv.id,
            summary=adv.summary,
            severity=adv.severity,
            feed=adv.feed,
            ecosystem=adv.ecosystem,
            package=adv.package,
            source_url=adv.source_url,
            published_at=adv.published_at,
            fetched_at=adv.fetched_at,
            match_basis=link.match_basis,
            match_value=link.match_value,
            match_confidence=float(link.match_confidence) if link.match_confidence else None,
            linked_at=link.linked_at,
        ),
        found=True,
    )


@router.get(
    "/servers/cve-history",
    response_model=ServerCVEHistoryListResponse,
    name="server_cve_history:list",
)
def list_servers_cve_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    severity: Optional[str] = Query(None, description="Filter: CRITICAL, HIGH, MEDIUM, LOW"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk tier"),
    db: Session = Depends(get_session),
) -> ServerCVEHistoryListResponse:
    """Return paginated list of servers that have CVE history."""
    base_q = (
        db.query(VulnLink.server_id)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
    )
    if severity:
        base_q = base_q.filter(VulnAdvisory.severity == severity)

    total = (
        base_q.with_entities(func.count(func.distinct(VulnLink.server_id)))
        .scalar()
        or 0
    )

    server_ids = (
        base_q.with_entities(func.distinct(VulnLink.server_id))
        .offset(skip)
        .limit(limit)
        .all()
    )
    server_ids = [sid[0] for sid in server_ids]

    results: List[ServerCVEListItem] = []
    for sid in server_ids:
        srv = (
            db.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id == sid)
            .first()
        )
        if risk_tier and (not srv or srv.risk_tier != risk_tier):
            continue

        sev_rows = (
            db.query(VulnAdvisory.severity, func.count(VulnAdvisory.id))
            .join(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
            .filter(VulnLink.server_id == sid)
            .group_by(VulnAdvisory.severity)
            .all()
        )
        sev_map: Dict[str, int] = {r[0] or "UNKNOWN": r[1] for r in sev_rows}

        total_cves = (
            db.query(func.count(func.distinct(VulnLink.advisory_id)))
            .filter(VulnLink.server_id == sid)
            .scalar()
            or 0
        )
        results.append(
            ServerCVEListItem(
                server_id=sid,
                name=srv.name if srv else None,
                registry_source=srv.registry_source if srv else None,
                risk_tier=srv.risk_tier if srv else None,
                total_cves=total_cves,
                critical_count=sev_map.get("CRITICAL", 0),
                high_count=sev_map.get("HIGH", 0),
                medium_count=sev_map.get("MEDIUM", 0),
                low_count=sev_map.get("LOW", 0),
            )
        )

    results.sort(key=lambda x: -x.critical_count)
    return ServerCVEHistoryListResponse(
        items=results,
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/cve-history/summary",
    response_model=CVEHistorySummary,
    name="server_cve_history:summary",
)
def cve_history_summary(
    db: Session = Depends(get_session),
) -> CVEHistorySummary:
    """Return global CVE history summary."""
    total_advisories = db.query(func.count(VulnAdvisory.id)).scalar() or 0
    total_links = db.query(func.count(VulnLink.id)).scalar() or 0
    servers_with_cves = (
        db.query(func.count(func.distinct(VulnLink.server_id)))
        .filter(VulnLink.advisory_id.isnot(None))
        .scalar()
        or 0
    )
    total_servers = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    sev_agg = (
        db.query(
            VulnAdvisory.severity,
            func.count(VulnAdvisory.id).label("cnt"),
        )
        .group_by(VulnAdvisory.severity)
        .all()
    )
    all_sevs = [s.severity for s in sev_agg]
    by_severity = _severity_counts(all_sevs)

    return CVEHistorySummary(
        total_servers=total_servers,
        servers_with_cves=servers_with_cves,
        total_advisories=total_advisories,
        total_links=total_links,
        by_severity=by_severity,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    _eng = StaticPool().create("sqlite:///:memory:")
    from sqlalchemy import create_engine
    _eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    if Base is not None:
        Base.metadata.create_all(bind=_eng)
    from sqlalchemy.orm import sessionmaker
    _TS = sessionmaker(bind=_eng, autoflush=False, autocommit=False)

    # Seed test data
    with _TS() as db:
        from sqlalchemy import text
        db.execute(text(
            """
            INSERT INTO mcp_server_registry (server_id, name, registry_source, risk_tier, url)
            VALUES
                ('srv1','Test Server 1','github','HIGH','https://github.com/srv1'),
                ('srv2','Test Server 2','npm','MEDIUM','https://npmjs.com/srv2'),
                ('srv3','Clean Server','github','LOW','https://github.com/srv3');
            """
        ))
        db.execute(text(
            """
            INSERT INTO vuln_advisories (id, feed, summary, severity, ecosystem, package, source_url, published_at, fetched_at)
            VALUES
                ('CVE-2023-0001','nvd','Critical RCE','CRITICAL','npm','evil-pkg','https://nvd/1','2023-01-01','2023-01-02'),
                ('CVE-2023-0002','nvd','High XSS','HIGH','npm','xss-pkg','https://nvd/2','2023-01-02','2023-01-02'),
                ('CVE-2023-0003','ghsa','Medium DoS','MEDIUM','PyPI','dos-pkg','https://ghsa/3','2023-01-03','2023-01-03'),
                ('CVE-2022-9999','nvd','Old Low','LOW','npm','old-pkg','https://nvd/old','2022-01-01','2022-01-02');
            """
        ))
        db.execute(text(
            """
            INSERT INTO vuln_links (advisory_id, server_id, match_basis, match_value, match_confidence)
            VALUES
                ('CVE-2023-0001','srv1','package_exact','evil-pkg',1.0),
                ('CVE-2023-0001','srv2','package_exact','evil-pkg',0.95),
                ('CVE-2023-0002','srv1','package_exact','xss-pkg',0.90),
                ('CVE-2023-0003','srv2','package_exact','dos-pkg',0.80),
                ('CVE-2022-9999','srv1','package_exact','old-pkg',1.0);
            """
        ))
        db.commit()

    _that_app = FastAPI()
    _that_app.include_router(router)

    def _override_session():
        s = _TS()
        try:
            yield s
        finally:
            s.close()

    if get_session is not None:
        _that_app.dependency_overrides[get_session] = _override_session

    _c = TestClient(_that_app)

    # Test global summary
    resp = _c.get("/api/cve-history/summary")
    assert resp.status_code == 200, f"Summary failed: {resp.status_code} {resp.text}"
    summary = resp.json()
    assert summary["total_advisories"] == 4
    assert summary["total_links"] == 5

    # Test get server history (365-day window -- CVE-2022 is excluded)
    resp = _c.get("/api/servers/srv1/cve-history")
    assert resp.status_code == 200, f"Get history failed: {resp.status_code} {resp.text}"
    detail = resp.json()
    assert detail["server_id"] == "srv1"
    assert detail["total_cves"] == 2, f"Expected 2, got {detail['total_cves']}"
    assert detail["by_severity"]["CRITICAL"] == 1
    assert detail["by_severity"]["HIGH"] == 1
    assert len(detail["advisories"]) == 2

    # Test with wider window to include CVE-2022
    resp = _c.get("/api/servers/srv1/cve-history?days=500")
    assert resp.status_code == 200
    assert resp.json()["total_cves"] == 3

    # Test 404 server
    resp = _c.get("/api/servers/nonexistent/cve-history")
    assert resp.status_code == 404

    # Test single CVE detail
    resp = _c.get("/api/servers/srv1/cve-history/CVE-2023-0001")
    assert resp.status_code == 200
    cve_detail = resp.json()
    assert cve_detail["cve_id"] == "CVE-2023-0001"
    assert cve_detail["found"] is True
    assert cve_detail["advisory"]["severity"] == "CRITICAL"

    # Test 404 CVE for a server
    resp = _c.get("/api/servers/srv3/cve-history/CVE-2023-0001")
    assert resp.status_code == 404

    # Test list endpoint
    resp = _c.get("/api/servers/cve-history")
    assert resp.status_code == 200, f"List failed: {resp.status_code} {resp.text}"
    payload = resp.json()
    assert isinstance(payload["items"], list)
    assert len(payload["items"]) == 2
    assert payload["items"][0]["server_id"] == "srv1"
    assert payload["items"][0]["critical_count"] == 1

    # Test severity filter
    resp = _c.get("/api/servers/cve-history?severity=CRITICAL")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2

    print("PASS")
