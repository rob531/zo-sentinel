# deps: fastapi, pydantic, sqlalchemy
"""Server CVE Info Service.

Lightweight CVE information endpoints per server and per CVE:
  GET /api/servers/{server_id}/cve-info       -- CVE inventory for one server
  GET /api/cve-info/{cve_id}                 -- Servers affected by one CVE

Auth: public.
Data: app Postgres via get_session + McpServerRegistry / VulnAdvisory / VulnLink.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

_repo_root = str(Path(__file__).resolve().parents[3])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["server_cve_info"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class ServerCveInfoItem(BaseModel):
    id: str
    summary: Optional[str] = None
    severity: Optional[str] = None
    feed: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    source_url: Optional[str] = None
    published_at: Optional[str] = None
    match_confidence: Optional[float] = None


class ServerCveInfoResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    total_advisories: int
    by_severity: Dict[str, int]
    advisories: List[ServerCveInfoItem]


class AffectedServerItem(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    registry_source: Optional[str] = None
    match_confidence: Optional[float] = None


class CveInfoResponse(BaseModel):
    cve_id: str
    summary: Optional[str] = None
    severity: Optional[str] = None
    feed: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    source_url: Optional[str] = None
    published_at: Optional[str] = None
    total_affected_servers: int
    affected_servers: List[AffectedServerItem]


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/servers/{server_id}/cve-info",
    response_model=ServerCveInfoResponse,
    name="server_cve_info:get",
)
def get_server_cve_info(
    server_id: str,
    days: int = Query(default=90, ge=1, le=730),
    db: Session = Depends(get_session),
) -> ServerCveInfoResponse:
    """Return CVE advisories linked to a specific server with severity breakdown."""
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Severity aggregation
    sev_rows = (
        db.query(
            VulnAdvisory.severity,
            func.count(VulnAdvisory.id).label("cnt"),
        )
        .join(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        .filter(
            VulnLink.server_id == server_id,
            VulnAdvisory.published_at >= cutoff,
        )
        .group_by(VulnAdvisory.severity)
        .all()
    )
    by_severity: Dict[str, int] = {r.severity or "UNKNOWN": r.cnt for r in sev_rows}

    # Advisory details
    links = (
        db.query(VulnLink, VulnAdvisory)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .filter(
            VulnLink.server_id == server_id,
            VulnAdvisory.published_at >= cutoff,
        )
        .order_by(VulnAdvisory.published_at.desc())
        .all()
    )

    advisories = []
    for link, adv in links:
        pub = adv.published_at
        advisories.append(
            ServerCveInfoItem(
                id=adv.id,
                summary=adv.summary,
                severity=adv.severity,
                feed=adv.feed,
                ecosystem=adv.ecosystem,
                package=adv.package,
                source_url=adv.source_url,
                published_at=(
                    pub.isoformat()
                    if isinstance(pub, datetime)
                    else str(pub) if pub else None
                ),
                match_confidence=(
                    float(link.match_confidence) if link.match_confidence is not None else None
                ),
            )
        )

    return ServerCveInfoResponse(
        server_id=server_id,
        name=server.name,
        registry_source=server.registry_source,
        risk_tier=server.risk_tier,
        total_advisories=len(advisories),
        by_severity=by_severity,
        advisories=advisories,
    )


@router.get(
    "/cve-info/{cve_id}",
    response_model=CveInfoResponse,
    name="server_cve_info:cve",
)
def get_cve_info(
    cve_id: str,
    db: Session = Depends(get_session),
) -> CveInfoResponse:
    """Return which servers are affected by a specific CVE."""
    adv = db.query(VulnAdvisory).filter(VulnAdvisory.id == cve_id).first()

    if not adv:
        raise HTTPException(status_code=404, detail="CVE not found")

    links = (
        db.query(VulnLink, McpServerRegistry)
        .join(McpServerRegistry, VulnLink.server_id == McpServerRegistry.server_id)
        .filter(VulnLink.advisory_id == cve_id)
        .all()
    )

    affected_servers = []
    for link, srv in links:
        affected_servers.append(
            AffectedServerItem(
                server_id=srv.server_id,
                name=srv.name,
                risk_tier=srv.risk_tier,
                registry_source=srv.registry_source,
                match_confidence=(
                    float(link.match_confidence) if link.match_confidence is not None else None
                ),
            )
        )

    pub = adv.published_at
    return CveInfoResponse(
        cve_id=cve_id,
        summary=adv.summary,
        severity=adv.severity,
        feed=adv.feed,
        ecosystem=adv.ecosystem,
        package=adv.package,
        source_url=adv.source_url,
        published_at=(
            pub.isoformat()
            if isinstance(pub, datetime)
            else str(pub) if pub else None
        ),
        total_affected_servers=len(affected_servers),
        affected_servers=affected_servers,
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

    # Test get server CVE info (default 90-day window excludes CVE-2022)
    resp = _c.get("/api/servers/srv1/cve-info")
    assert resp.status_code == 200, f"Get srv1 failed: {resp.status_code} {resp.text}"
    detail = resp.json()
    assert detail["server_id"] == "srv1"
    assert detail["total_advisories"] == 2, f"Expected 2 (excl old), got {detail['total_advisories']}"
    assert detail["by_severity"]["CRITICAL"] == 1
    assert detail["by_severity"]["HIGH"] == 1
    assert len(detail["advisories"]) == 2

    # Wider window to include CVE-2022
    resp = _c.get("/api/servers/srv1/cve-info?days=500")
    assert resp.status_code == 200
    assert resp.json()["total_advisories"] == 3

    # srv2: one advisory
    resp = _c.get("/api/servers/srv2/cve-info")
    assert resp.status_code == 200
    assert resp.json()["total_advisories"] == 1
    assert resp.json()["by_severity"]["MEDIUM"] == 1

    # srv3: no advisories
    resp = _c.get("/api/servers/srv3/cve-info")
    assert resp.status_code == 200
    assert resp.json()["total_advisories"] == 0
    assert resp.json()["advisories"] == []

    # 404 for unknown server
    resp = _c.get("/api/servers/nonexistent/cve-info")
    assert resp.status_code == 404

    # days validation
    resp = _c.get("/api/servers/srv1/cve-info?days=0")
    assert resp.status_code == 422

    # Test per-CVE endpoint
    resp = _c.get("/api/cve-info/CVE-2023-0001")
    assert resp.status_code == 200, f"CVE-2023-0001 failed: {resp.status_code} {resp.text}"
    cve = resp.json()
    assert cve["cve_id"] == "CVE-2023-0001"
    assert cve["severity"] == "CRITICAL"
    assert cve["total_affected_servers"] == 2
    assert {s["server_id"] for s in cve["affected_servers"]} == {"srv1", "srv2"}

    # CVE shared between srv1 and srv2
    resp = _c.get("/api/cve-info/CVE-2023-0003")
    assert resp.status_code == 200
    assert resp.json()["total_affected_servers"] == 1
    assert resp.json()["affected_servers"][0]["server_id"] == "srv2"

    # 404 for unknown CVE
    resp = _c.get("/api/cve-info/CVE-9999-9999")
    assert resp.status_code == 404

    print("PASS")
