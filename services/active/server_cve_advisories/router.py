# deps: fastapi, pydantic, sqlalchemy
"""Server CVE Advisories Service.

Returns CVE advisories linked to a server, with severity breakdown and match provenance.
Reads from app Postgres (McpServerRegistry, VulnAdvisory, VulnLink) via get_session.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["server_cve_advisories"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class AdvisoryDetail(BaseModel):
    id: str
    feed: Optional[str] = None
    summary: Optional[str] = None
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    source_url: Optional[str] = None
    published_at: Optional[str] = None
    match_basis: Optional[str] = None
    match_value: Optional[str] = None
    match_confidence: Optional[float] = None


class ServerCveAdvisoriesResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    total_advisories: int
    by_severity: Dict[str, int]
    advisories: List[AdvisoryDetail]


class ServerCveListItem(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    total_advisories: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/servers/{server_id}/cve-advisories",
    response_model=ServerCveAdvisoriesResponse,
    name="server_cve_advisories:get",
)
def get_server_cve_advisories(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ServerCveAdvisoriesResponse:
    """Return all CVE advisories linked to a specific server, with severity breakdown."""
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Severity breakdown
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

    # Linked advisories
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
        published = adv.published_at
        advisories.append(
            AdvisoryDetail(
                id=adv.id,
                feed=adv.feed,
                summary=adv.summary,
                severity=adv.severity,
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

    return ServerCveAdvisoriesResponse(
        server_id=server_id,
        name=server.name,
        registry_source=server.registry_source,
        total_advisories=len(advisories),
        by_severity=by_severity,
        advisories=advisories,
    )


@router.get(
    "/servers/cve-advisories",
    response_model=List[ServerCveListItem],
    name="server_cve_advisories:list",
)
def list_servers_with_advisories(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    severity: Optional[str] = Query(None, description="Filter: CRITICAL, HIGH, MEDIUM, LOW"),
    db: Session = Depends(get_session),
) -> List[ServerCveListItem]:
    """Return all servers that have at least one linked CVE advisory, with severity counts."""
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

    results: List[ServerCveListItem] = []
    for sid in server_ids:
        srv = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == sid).first()
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
            ServerCveListItem(
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

    return results


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

    # Test get server advisories (default 30-day window -- CVE-2022 is excluded)
    resp = _c.get("/api/servers/srv1/cve-advisories")
    assert resp.status_code == 200, f"Get advisories failed: {resp.status_code} {resp.text}"
    detail = resp.json()
    assert detail["server_id"] == "srv1"
    assert detail["total_advisories"] == 2, f"Expected 2 (excl old), got {detail['total_advisories']}"
    assert detail["by_severity"]["CRITICAL"] == 1
    assert detail["by_severity"]["HIGH"] == 1
    assert len(detail["advisories"]) == 2

    # Test with wider window to include CVE-2022
    resp = _c.get("/api/servers/srv1/cve-advisories?days=500")
    assert resp.status_code == 200
    assert resp.json()["total_advisories"] == 3

    # Test 404
    resp = _c.get("/api/servers/nonexistent/cve-advisories")
    assert resp.status_code == 404

    # Test list endpoint
    resp = _c.get("/api/servers/cve-advisories")
    assert resp.status_code == 200, f"List failed: {resp.status_code} {resp.text}"
    payload = resp.json()
    assert isinstance(payload, list)
    assert len(payload) == 2, f"Expected 2 servers with advisories, got {len(payload)}"
    assert payload[0]["server_id"] == "srv1"
    assert payload[0]["critical_count"] == 1
    assert payload[0]["total_advisories"] == 3  # all time

    # Test severity filter
    resp = _c.get("/api/servers/cve-advisories?severity=CRITICAL")
    assert resp.status_code == 200
    assert len(resp.json()) == 2  # both srv1 and srv2 have CRITICAL through shared CVE

    print("PASS")
