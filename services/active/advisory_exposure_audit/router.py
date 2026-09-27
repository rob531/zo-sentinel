# deps: fastapi, sqlalchemy, pydantic
"""Advisory Exposure Audit Service.

Provides audit-level summary of vulnerability advisory exposure across the
MCP server registry: coverage rate, severity breakdown, per-server advisory
counts, and the top-10 riskiest servers by CVE exposure.

Auth: public.
Data: app Postgres via get_session + (McpServerRegistry, VulnAdvisory, VulnLink).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure repo root is on path for `python router.py` self-test
_repo_root = str(Path(__file__).resolve().parents[2])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["advisory_exposure_audit"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #


class ServerExposureItem(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    worst_severity: Optional[str] = None
    advisory_count: int


class SeverityBreakdown(BaseModel):
    CRITICAL: int
    HIGH: int
    MEDIUM: int
    LOW: int
    NONE: int


class AdvisoryExposureAuditResponse(BaseModel):
    audit_at: str
    coverage_pct: float
    total_servers: int
    servers_with_advisories: int
    total_advisories: int
    total_links: int
    avg_advisories_per_affected_server: float
    critical_exposed_servers: int
    high_exposed_servers: int
    severity_breakdown: SeverityBreakdown
    top_10_riskiest: list[ServerExposureItem]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


_SEVERITY_WEIGHT = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, None: 0}


def _severity_key(severity: Optional[str]) -> int:
    return _SEVERITY_WEIGHT.get(severity, 0)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/audit/advisory-exposure",
    response_model=AdvisoryExposureAuditResponse,
    name="advisory_exposure_audit:get",
)
def get_advisory_exposure_audit(
    db: Session = Depends(get_session),
) -> AdvisoryExposureAuditResponse:
    """Return a global audit of advisory exposure across all servers."""
    total_servers: int = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    servers_with_advisories: int = (
        db.query(func.count(func.distinct(VulnLink.server_id)))
        .scalar()
        or 0
    )

    coverage_pct = (
        round(servers_with_advisories / total_servers * 100, 2)
        if total_servers > 0
        else 0.0
    )

    total_advisories: int = db.query(func.count(VulnAdvisory.id)).scalar() or 0
    total_links: int = db.query(func.count(VulnLink.id)).scalar() or 0

    avg_advisories_per_affected_server = (
        round(total_links / servers_with_advisories, 2)
        if servers_with_advisories > 0
        else 0.0
    )

    critical_exposed_servers: int = (
        db.query(func.count(func.distinct(VulnLink.server_id)))
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .filter(VulnAdvisory.severity == "CRITICAL")
        .scalar()
        or 0
    )

    high_exposed_servers: int = (
        db.query(func.count(func.distinct(VulnLink.server_id)))
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .filter(VulnAdvisory.severity == "HIGH")
        .scalar()
        or 0
    )

    # Per-server severity aggregation
    server_rows = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            func.max(VulnAdvisory.severity).label("worst_severity"),
            func.count(VulnLink.id).label("advisory_count"),
        )
        .outerjoin(VulnLink, VulnLink.server_id == McpServerRegistry.server_id)
        .outerjoin(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .group_by(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
        .all()
    )

    # Severity breakdown by worst severity per server
    breakdown = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "NONE": 0}
    for row in server_rows:
        key = row.worst_severity if row.worst_severity else "NONE"
        if key in breakdown:
            breakdown[key] += 1

    # Rank: worst severity first, then by advisory count desc
    ranked = sorted(server_rows, key=lambda r: (_severity_key(r.worst_severity), r.advisory_count or 0), reverse=True)

    top_10 = [
        ServerExposureItem(
            server_id=r.server_id,
            name=r.name,
            registry_source=r.registry_source,
            risk_tier=r.risk_tier,
            worst_severity=r.worst_severity,
            advisory_count=r.advisory_count or 0,
        )
        for r in ranked[:10]
    ]

    return AdvisoryExposureAuditResponse(
        audit_at=datetime.now(timezone.utc).isoformat(),
        coverage_pct=coverage_pct,
        total_servers=total_servers,
        servers_with_advisories=servers_with_advisories,
        total_advisories=total_advisories,
        total_links=total_links,
        avg_advisories_per_affected_server=avg_advisories_per_affected_server,
        critical_exposed_servers=critical_exposed_servers,
        high_exposed_servers=high_exposed_servers,
        severity_breakdown=SeverityBreakdown(**breakdown),
        top_10_riskiest=top_10,
    )


@router.get(
    "/audit/advisory-exposure/severity",
    response_model=SeverityBreakdown,
    name="advisory_exposure_audit:severity_breakdown",
)
def get_severity_breakdown(
    db: Session = Depends(get_session),
) -> SeverityBreakdown:
    """Return server counts grouped by their worst-case advisory severity."""
    rows = (
        db.query(
            func.max(VulnAdvisory.severity).label("worst_severity"),
        )
        .select_from(McpServerRegistry)
        .outerjoin(VulnLink, VulnLink.server_id == McpServerRegistry.server_id)
        .outerjoin(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .group_by(McpServerRegistry.server_id)
        .all()
    )

    breakdown = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "NONE": 0}
    for (worst,) in rows:
        key = worst if worst else "NONE"
        if key in breakdown:
            breakdown[key] += 1

    return SeverityBreakdown(**breakdown)


@router.get(
    "/audit/advisory-exposure/servers",
    response_model=list[ServerExposureItem],
    name="advisory_exposure_audit:server_list",
)
def list_server_exposure(
    severity: Optional[str] = Query(None, description="Filter: CRITICAL, HIGH, MEDIUM, LOW"),
    min_advisories: int = Query(0, ge=0, description="Minimum advisory count"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> list[ServerExposureItem]:
    """Return all servers with their worst severity and advisory count, optionally filtered."""
    q = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            func.max(VulnAdvisory.severity).label("worst_severity"),
            func.count(VulnLink.id).label("advisory_count"),
        )
        .outerjoin(VulnLink, VulnLink.server_id == McpServerRegistry.server_id)
        .outerjoin(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .group_by(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
    )

    if severity:
        q = q.having(func.max(VulnAdvisory.severity) == severity)
    if min_advisories > 0:
        q = q.having(func.count(VulnLink.id) >= min_advisories)

    rows = q.order_by(func.count(VulnLink.id).desc()).limit(limit).all()

    return [
        ServerExposureItem(
            server_id=r.server_id,
            name=r.name,
            registry_source=r.registry_source,
            risk_tier=r.risk_tier,
            worst_severity=r.worst_severity,
            advisory_count=r.advisory_count or 0,
        )
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from pathlib import Path

    _repo_root = str(Path(__file__).resolve().parents[2])
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
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
                ('srv3','Clean Server','github','LOW','https://github.com/srv3'),
                ('srv4','Another Clean','npm','LOW','https://npmjs.com/srv4');
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
                ('CVE-2022-9999','nvd','Low Info','LOW','npm','old-pkg','https://nvd/old','2022-01-01','2022-01-02');
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

    # Test main audit endpoint
    resp = _c.get("/api/audit/advisory-exposure")
    assert resp.status_code == 200, f"Audit failed: {resp.status_code} {resp.text}"
    data = resp.json()

    assert data["total_servers"] == 4
    assert data["servers_with_advisories"] == 2, f"Expected 2, got {data['servers_with_advisories']}"
    assert abs(data["coverage_pct"] - 50.0) < 0.01, f"Expected 50.0, got {data['coverage_pct']}"
    assert data["total_advisories"] == 4
    assert data["total_links"] == 5
    assert data["critical_exposed_servers"] == 2
    assert data["high_exposed_servers"] == 1

    sev = data["severity_breakdown"]
    assert sev["CRITICAL"] == 2, f"CRITICAL should be 2, got {sev}"
    assert sev["HIGH"] == 0, f"HIGH should be 0 (none have HIGH as worst), got {sev}"
    assert sev["NONE"] == 2, f"NONE should be 2 (srv3 and srv4), got {sev}"

    top = data["top_10_riskiest"]
    assert len(top) == 2
    assert top[0]["server_id"] == "srv1"   # worst: CRITICAL, count=3
    assert top[1]["server_id"] == "srv2"   # worst: CRITICAL, count=2

    # Test severity breakdown endpoint
    resp2 = _c.get("/api/audit/advisory-exposure/severity")
    assert resp2.status_code == 200, f"Severity breakdown failed: {resp2.status_code}"
    sev2 = resp2.json()
    assert sev2["CRITICAL"] == 2
    assert sev2["NONE"] == 2

    # Test server list endpoint
    resp3 = _c.get("/api/audit/advisory-exposure/servers")
    assert resp3.status_code == 200
    servers = resp3.json()
    assert len(servers) == 4
    assert servers[0]["server_id"] == "srv1"
    assert servers[0]["advisory_count"] == 3

    # Test severity filter
    resp4 = _c.get("/api/audit/advisory-exposure/servers?severity=CRITICAL")
    assert resp4.status_code == 200
    filtered = resp4.json()
    assert len(filtered) == 2
    assert all(s["worst_severity"] == "CRITICAL" for s in filtered)

    # Test min_advisories filter
    resp5 = _c.get("/api/audit/advisory-exposure/servers?min_advisories=3")
    assert resp5.status_code == 200
    filtered2 = resp5.json()
    assert len(filtered2) == 1
    assert filtered2[0]["server_id"] == "srv1"

    print("PASS")
