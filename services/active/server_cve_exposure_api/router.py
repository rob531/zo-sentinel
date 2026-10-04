# deps: fastapi, pydantic, sqlalchemy
"""Server CVE Exposure API.

Returns per-server CVE exposure summaries: advisory counts, severity breakdown,
and linked advisories with match provenance.
"""
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["server_cve_exposure_api"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class AdvisoryExposure(BaseModel):
    id: str
    summary: Optional[str] = None
    severity: Optional[str] = None
    feed: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    source_url: Optional[str] = None
    published_at: Optional[str] = None
    match_basis: Optional[str] = None
    match_value: Optional[str] = None
    match_confidence: Optional[float] = None


class ServerCveExposure(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    total_advisories: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    by_severity: Dict[str, int]
    advisories: List[AdvisoryExposure]


class CveExposureListItem(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    total_advisories: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int


class CveExposureSummary(BaseModel):
    total_servers: int
    servers_with_cves: int
    total_advisories: int
    total_links: int
    by_severity: Dict[str, int]


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/servers/{server_id}/cve-exposure",
    response_model=ServerCveExposure,
    name="server_cve_exposure_api:get",
)
def get_server_cve_exposure(
    server_id: str,
    days: int = Query(default=90, ge=1, le=730),
    db: Session = Depends(get_session),
) -> ServerCveExposure:
    """Return CVE exposure details for a specific server."""
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    cutoff = datetime.now().replace(tzinfo=None) - __import__("datetime").timedelta(days=days)

    sev_counts = (
        db.query(
            VulnAdvisory.severity,
            func.count(VulnAdvisory.id).label("cnt"),
        )
        .join(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
        .filter(
            VulnLink.server_id == server_id,
            VulnAdvisory.published_at >= cutoff,
        )
        .group_by(VulnAdvisory.severity)
        .all()
    )
    by_severity = {s.severity or "UNKNOWN": s.cnt for s in sev_counts}

    links = (
        db.query(VulnLink, VulnAdvisory)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .filter(
            VulnLink.server_id == server_id,
            VulnAdvisory.published_at >= cutoff,
        )
        .order_by(VulnAdvisory.published_at.desc().nullslast())
        .all()
    )

    advisories = []
    for link, adv in links:
        published = adv.published_at
        advisories.append(
            AdvisoryExposure(
                id=adv.id,
                summary=adv.summary,
                severity=adv.severity,
                feed=adv.feed,
                ecosystem=adv.ecosystem,
                package=adv.package,
                source_url=adv.source_url,
                published_at=(
                    published.isoformat()
                    if isinstance(published, datetime)
                    else str(published) if published else None
                ),
                match_basis=link.match_basis,
                match_value=link.match_value,
                match_confidence=float(link.match_confidence) if link.match_confidence else None,
            )
        )

    return ServerCveExposure(
        server_id=server_id,
        name=server.name,
        registry_source=server.registry_source,
        risk_tier=server.risk_tier,
        total_advisories=len(advisories),
        critical_count=by_severity.get("CRITICAL", 0),
        high_count=by_severity.get("HIGH", 0),
        medium_count=by_severity.get("MEDIUM", 0),
        low_count=by_severity.get("LOW", 0),
        by_severity=by_severity,
        advisories=advisories,
    )


@router.get(
    "/servers/cve-exposure",
    response_model=List[CveExposureListItem],
    name="server_cve_exposure_api:list",
)
def list_servers_cve_exposure(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    severity: Optional[str] = Query(None, description="Filter: CRITICAL, HIGH, MEDIUM, LOW"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk tier"),
    db: Session = Depends(get_session),
) -> List[CveExposureListItem]:
    """Return all servers with CVE exposure summaries, sorted by critical count."""
    base_q = (
        db.query(VulnLink.server_id)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
    )
    if severity:
        base_q = base_q.filter(VulnAdvisory.severity == severity)

    server_ids = (
        base_q.with_entities(func.distinct(VulnLink.server_id))
        .offset(skip)
        .limit(limit)
        .all()
    )
    server_ids = [sid[0] for sid in server_ids]

    results: List[CveExposureListItem] = []
    for sid in server_ids:
        srv = (
            db.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id == sid)
            .first()
        )
        if risk_tier and (not srv or srv.risk_tier != risk_tier):
            continue
        sev_map: Dict[str, int] = {}
        for row in (
            db.query(VulnAdvisory.severity, func.count(VulnAdvisory.id))
            .join(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
            .filter(VulnLink.server_id == sid)
            .group_by(VulnAdvisory.severity)
            .all()
        ):
            sev_map[row[0] or "UNKNOWN"] = row[1]

        total = (
            db.query(func.count(func.distinct(VulnLink.advisory_id)))
            .filter(VulnLink.server_id == sid)
            .scalar()
            or 0
        )
        results.append(
            CveExposureListItem(
                server_id=sid,
                name=srv.name if srv else None,
                registry_source=srv.registry_source if srv else None,
                risk_tier=srv.risk_tier if srv else None,
                total_advisories=total,
                critical_count=sev_map.get("CRITICAL", 0),
                high_count=sev_map.get("HIGH", 0),
                medium_count=sev_map.get("MEDIUM", 0),
                low_count=sev_map.get("LOW", 0),
            )
        )

    # Sort by critical desc
    results.sort(key=lambda x: -x.critical_count)
    return results


@router.get(
    "/cve-exposure/summary",
    response_model=CveExposureSummary,
    name="server_cve_exposure_api:summary",
)
def cve_exposure_summary(
    db: Session = Depends(get_session),
) -> CveExposureSummary:
    """Return global CVE exposure summary."""
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
    by_severity = {s.severity or "UNKNOWN": s.cnt for s in sev_agg}

    return CveExposureSummary(
        total_servers=total_servers,
        servers_with_cves=servers_with_cves,
        total_advisories=total_advisories,
        total_links=total_links,
        by_severity=by_severity,
    )


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
                ('CVE-2023-0003','ghsa','Medium DoS','MEDIUM','PyPI','dos-pkg','https://ghsa/3','2023-01-03','2023-01-03'),
                ('CVE-2022-9999','nvd','Old Low','LOW','npm','old-pkg','https://nvd/old','2022-01-01','2022-01-02');
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
                ('CVE-2023-0002','srv1','package_exact','xss-pkg',0.90),
                ('CVE-2023-0003','srv2','package_exact','dos-pkg',0.80),
                ('CVE-2022-9999','srv1','package_exact','old-pkg',1.0);
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

    # Test global summary
    resp = _c.get("/api/cve-exposure/summary")
    assert resp.status_code == 200, f"Summary failed: {resp.status_code} {resp.text}"
    summary = resp.json()
    assert summary["total_advisories"] == 4
    assert summary["total_links"] == 5

    # Test get server exposure (90-day default window -- CVE-2022 is excluded)
    resp = _c.get("/api/servers/srv1/cve-exposure")
    assert resp.status_code == 200, f"Get exposure failed: {resp.status_code} {resp.text}"
    detail = resp.json()
    assert detail["server_id"] == "srv1"
    assert detail["total_advisories"] == 2, f"Expected 2 (excl old), got {detail['total_advisories']}"
    assert detail["by_severity"]["CRITICAL"] == 1
    assert detail["by_severity"]["HIGH"] == 1
    assert len(detail["advisories"]) == 2
    assert detail["critical_count"] == 1
    assert detail["high_count"] == 1

    # Test with wider window to include CVE-2022
    resp = _c.get("/api/servers/srv1/cve-exposure?days=500")
    assert resp.status_code == 200
    assert resp.json()["total_advisories"] == 3

    # Test 404
    resp = _c.get("/api/servers/nonexistent/cve-exposure")
    assert resp.status_code == 404

    # Test list endpoint
    resp = _c.get("/api/servers/cve-exposure")
    assert resp.status_code == 200, f"List failed: {resp.status_code} {resp.text}"
    payload = resp.json()
    assert isinstance(payload, list)
    assert len(payload) == 2, f"Expected 2 servers with advisories, got {len(payload)}"
    assert payload[0]["server_id"] == "srv1"
    assert payload[0]["critical_count"] == 1
    assert payload[0]["total_advisories"] == 3  # all time

    # Test severity filter
    resp = _c.get("/api/servers/cve-exposure?severity=CRITICAL")
    assert resp.status_code == 200
    assert len(resp.json()) == 2  # both srv1 and srv2 have CRITICAL through shared CVE

    print("PASS")
