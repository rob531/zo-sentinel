# deps: fastapi, sqlalchemy, pydantic
"""CVE Server Impact API.

GET /api/cve/{cve_id}/servers
  Returns which MCP servers are affected by a given CVE: advisory metadata
  (severity, ecosystem, package, summary) and the list of linked servers with
  their name, risk_tier, and match_confidence.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on vuln_advisories,
  vuln_links, mcp_server_registry.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["cve_server_impact_api"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class ServerImpactItem(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    match_basis: Optional[str] = None
    match_value: Optional[str] = None
    match_confidence: Optional[float] = None


class CveServerImpactResponse(BaseModel):
    cve_id: str
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    summary: Optional[str] = None
    total_servers: int
    servers: list[ServerImpactItem]


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/cve/{cve_id}/servers",
    response_model=CveServerImpactResponse,
    name="cve_server_impact_api:get",
    responses={404: {"description": "CVE not found"}},
)
def get_cve_server_impact(
    cve_id: str,
    package: Optional[str] = Query(
        default=None,
        description="Filter links by package name",
    ),
    db: Session = Depends(get_session),
) -> CveServerImpactResponse:
    """Return advisory metadata and all servers linked to the given CVE."""
    # Fetch advisory
    advisory = (
        db.query(VulnAdvisory)
        .filter(VulnAdvisory.id == cve_id)
        .first()
    )
    if advisory is None:
        raise HTTPException(status_code=404, detail=f"CVE {cve_id} not found")

    # Query linked servers
    link_query = (
        db.query(VulnLink, McpServerRegistry)
        .join(McpServerRegistry, VulnLink.server_id == McpServerRegistry.server_id)
        .filter(VulnLink.advisory_id == cve_id)
    )
    if package:
        link_query = link_query.filter(VulnAdvisory.package == package)

    link_rows = link_query.all()

    servers: list[ServerImpactItem] = []
    for link, srv in link_rows:
        servers.append(
            ServerImpactItem(
                server_id=srv.server_id,
                name=srv.name,
                registry_source=srv.registry_source,
                risk_tier=srv.risk_tier,
                match_basis=link.match_basis,
                match_value=link.match_value,
                match_confidence=float(link.match_confidence)
                if link.match_confidence is not None
                else None,
            )
        )

    return CveServerImpactResponse(
        cve_id=cve_id,
        severity=advisory.severity,
        ecosystem=advisory.ecosystem,
        package=advisory.package,
        summary=advisory.summary,
        total_servers=len(servers),
        servers=servers,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import sys
    from datetime import datetime
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    _eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_eng)
    _TS = sessionmaker(bind=_eng, autoflush=False, autocommit=False)

    # Seed test data: 2 advisories, 3 links across 3 servers
    with _TS() as db:
        db.execute(
            text(
                "INSERT INTO mcp_server_registry "
                "(server_id, name, registry_source, risk_tier, url) VALUES "
                "('srv1','Server Alpha','github','HIGH','https://github.com/alpha'),"
                "('srv2','Server Beta','npm','MEDIUM','https://npmjs.com/beta'),"
                "('srv3','Server Gamma','github','LOW','https://github.com/gamma')"
            )
        )
        db.execute(
            text(
                "INSERT INTO vuln_advisories "
                "(id, feed, summary, severity, ecosystem, package, source_url, published_at, fetched_at) VALUES "
                "('CVE-2024-0001','nvd','Prototype pollution in lodash','HIGH','npm','lodash','https://nvd/1','2024-01-01','2024-01-02'),"
                "('CVE-2024-0002','nvd','SSRF in requests','CRITICAL','pip','requests','https://nvd/2','2024-02-01','2024-02-02')"
            )
        )
        db.execute(
            text(
                "INSERT INTO vuln_links "
                "(advisory_id, server_id, match_basis, match_value, match_confidence) VALUES "
                "('CVE-2024-0001','srv1','package_exact','lodash',0.95),"
                "('CVE-2024-0001','srv2','package_exact','lodash',0.80),"
                "('CVE-2024-0002','srv3','package_exact','requests',0.90)"
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

    # Happy path
    resp = _c.get("/api/cve/CVE-2024-0001/servers")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert len(data["servers"]) >= 1, f"Expected >=1 servers, got {len(data['servers'])}"
    assert "severity" in data, "Response missing severity"
    assert data["severity"] == "HIGH"
    for srv in data["servers"]:
        assert "risk_tier" in srv, "Server missing risk_tier"

    # Package filter
    resp2 = _c.get("/api/cve/CVE-2024-0001/servers?package=lodash")
    assert resp2.status_code == 200, f"Filter failed: {resp2.status_code}"
    data2 = resp2.json()
    assert len(data2["servers"]) == 2, f"Expected 2 servers for lodash, got {len(data2['servers'])}"

    # Package filter returns empty list for non-matching
    resp3 = _c.get("/api/cve/CVE-2024-0001/servers?package=requests")
    assert resp3.status_code == 200
    assert len(resp3.json()["servers"]) == 0

    # 404 for unknown CVE
    resp4 = _c.get("/api/cve/CVE-9999-9999/servers")
    assert resp4.status_code == 404, f"Expected 404 for unknown CVE, got {resp4.status_code}"

    # Another CVE
    resp5 = _c.get("/api/cve/CVE-2024-0002/servers")
    assert resp5.status_code == 200
    data5 = resp5.json()
    assert data5["severity"] == "CRITICAL"
    assert len(data5["servers"]) >= 1

    print("PASS")
