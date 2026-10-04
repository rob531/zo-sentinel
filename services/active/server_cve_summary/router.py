# deps: fastapi, sqlalchemy, pydantic, requests
"""Server CVE Summary Service.

Returns CVE exposure summaries per server: advisory counts, severity breakdown,
and linked advisories with provenance.
"""

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["server_cve_summary"])


class ServerCveSummary(BaseModel):
    server_id: str
    name: Optional[str]
    registry_source: Optional[str]
    total_advisories: int
    by_severity: Dict[str, int]
    advisories: List[Dict]


class ServerCveListItem(BaseModel):
    server_id: str
    name: Optional[str]
    registry_source: Optional[str]
    total_advisories: int
    critical_count: int
    high_count: int


@router.get(
    "/servers/cve-summary",
    response_model=List[ServerCveListItem],
    name="server_cve_summary:list",
)
def list_server_cve_summaries(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> List[ServerCveListItem]:
    """
    Return a paginated list of servers with CVE exposure summaries,
    sorted by critical advisory count descending.
    """
    # Subquery: count advisories per severity per server
    severity_counts = (
        db.query(
            VulnLink.server_id,
            VulnAdvisory.severity,
            func.count(VulnAdvisory.id).label("cnt"),
        )
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .group_by(VulnLink.server_id, VulnAdvisory.severity)
        .subquery()
    )

    # Get servers that have any advisories, with counts
    servers_with_cves = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            func.count(func.distinct(VulnLink.advisory_id)).label("total"),
        )
        .outerjoin(VulnLink, McpServerRegistry.server_id == VulnLink.server_id)
        .group_by(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
        )
        .having(func.count(func.distinct(VulnLink.advisory_id)) > 0)
        .order_by(func.count(func.distinct(VulnLink.advisory_id)).desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    results = []
    for row in servers_with_cves:
        # Get critical and high counts for this server
        sev_counts = (
            db.query(
                VulnAdvisory.severity,
                func.count(VulnAdvisory.id).label("cnt"),
            )
            .join(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
            .filter(VulnLink.server_id == row.server_id)
            .group_by(VulnAdvisory.severity)
            .all()
        )
        sev_map = {s.severity or "UNKNOWN": s.cnt for s in sev_counts}
        results.append(
            ServerCveListItem(
                server_id=row.server_id,
                name=row.name,
                registry_source=row.registry_source,
                total_advisories=row.total or 0,
                critical_count=sev_map.get("CRITICAL", 0),
                high_count=sev_map.get("HIGH", 0),
            )
        )

    return results


@router.get(
    "/servers/{server_id}/cve-summary",
    response_model=ServerCveSummary,
    name="server_cve_summary:get",
)
def get_server_cve_summary(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerCveSummary:
    """
    Return a detailed CVE exposure summary for a specific server,
    including per-severity counts and linked advisories.
    """
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Server not found")

    # Get severity counts
    sev_counts = (
        db.query(
            VulnAdvisory.severity,
            func.count(VulnAdvisory.id).label("cnt"),
        )
        .join(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
        .filter(VulnLink.server_id == server_id)
        .group_by(VulnAdvisory.severity)
        .all()
    )
    by_severity = {s.severity or "UNKNOWN": s.cnt for s in sev_counts}

    # Get linked advisories with match details
    links = (
        db.query(VulnLink, VulnAdvisory)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .filter(VulnLink.server_id == server_id)
        .order_by(VulnAdvisory.published_at.desc().nullslast())
        .all()
    )

    advisories: List[Dict] = []
    for link, adv in links:
        published = adv.published_at
        advisories.append(
            {
                "id": adv.id,
                "summary": adv.summary,
                "severity": adv.severity,
                "feed": adv.feed,
                "source_url": adv.source_url,
                "published_at": (
                    published.isoformat()
                    if isinstance(published, datetime)
                    else str(published) if published else None
                ),
                "match_basis": link.match_basis,
                "match_value": link.match_value,
            }
        )

    total_advisories = len(advisories)

    return ServerCveSummary(
        server_id=server_id,
        name=server.name,
        registry_source=server.registry_source,
        total_advisories=total_advisories,
        by_severity=by_severity,
        advisories=advisories,
    )


if __name__ == "__main__":
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        # Insert test servers
        db.execute(
            text(
                """
            INSERT INTO mcp_server_registry (server_id, name, registry_source, url)
            VALUES
                ('srv1', 'Test Server 1', 'github', 'https://github.com/test/srv1'),
                ('srv2', 'Test Server 2', 'npm', 'https://npmjs.com/srv2');
            """
            )
        )
        # Insert test advisories
        db.execute(
            text(
                """
            INSERT INTO vuln_advisories (id, feed, summary, severity, source_url, published_at)
            VALUES
                ('CVE-2023-0001', 'nvd', 'Critical vuln', 'CRITICAL',
                 'https://nvd.nist.gov/vuln/detail/CVE-2023-0001', '2023-01-01T00:00:00'),
                ('CVE-2023-0002', 'nvd', 'High vuln', 'HIGH',
                 'https://nvd.nist.gov/vuln/detail/CVE-2023-0002', '2023-01-02T00:00:00'),
                ('CVE-2023-0003', 'ghsa', 'Medium vuln', 'MEDIUM',
                 'https://github.com/advisories/CVE-2023-0003', '2023-01-03T00:00:00');
            """
            )
        )
        # Insert test links
        db.execute(
            text(
                """
            INSERT INTO vuln_links (advisory_id, server_id, match_basis, match_value, match_confidence)
            VALUES
                ('CVE-2023-0001', 'srv1', 'package_exact', 'pkg1', 1.0),
                ('CVE-2023-0002', 'srv1', 'package_exact', 'pkg1', 1.0),
                ('CVE-2023-0003', 'srv2', 'package_exact', 'pkg3', 1.0);
            """
            )
        )
        db.commit()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    client = TestClient(app)

    # Test list endpoint
    resp = client.get("/api/servers/cve-summary")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    assert isinstance(payload, list), "Expected list response"
    assert len(payload) == 2, f"Expected 2 servers, got {len(payload)}"
    assert payload[0]["server_id"] == "srv1", "srv1 should be first (has most advisories)"

    # Test detail endpoint
    resp = client.get("/api/servers/srv1/cve-summary")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    detail = resp.json()
    assert detail["server_id"] == "srv1"
    assert detail["total_advisories"] == 2
    assert detail["by_severity"]["CRITICAL"] == 1
    assert detail["by_severity"]["HIGH"] == 1
    assert len(detail["advisories"]) == 2

    # Test 404
    resp = client.get("/api/servers/nonexistent/cve-summary")
    assert resp.status_code == 404

    print("PASS")
