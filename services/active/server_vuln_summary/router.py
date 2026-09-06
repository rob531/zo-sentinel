# deps: fastapi, sqlalchemy, pydantic
"""Server Vulnerability Summary Service.

Returns vulnerability exposure summaries per server: advisory counts, severity breakdown,
and linked advisories with provenance.
"""

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["server_vuln_summary"])


class ServerVulnSummary(BaseModel):
    server_id: str
    name: Optional[str]
    registry_source: Optional[str]
    total_advisories: int
    by_severity: Dict[str, int]
    advisories: List[Dict]


class ServerVulnListItem(BaseModel):
    server_id: str
    name: Optional[str]
    registry_source: Optional[str]
    total_advisories: int
    critical_count: int
    high_count: int
    medium_count: int


@router.get(
    "/servers/vuln-summary",
    response_model=List[ServerVulnListItem],
    name="server_vuln_summary:list",
)
def list_server_vuln_summaries(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> List[ServerVulnListItem]:
    """Return a paginated list of servers with vulnerability exposure summaries,
    sorted by total advisory count descending.
    """
    servers_with_vulns = (
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
    for row in servers_with_vulns:
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
            ServerVulnListItem(
                server_id=row.server_id,
                name=row.name,
                registry_source=row.registry_source,
                total_advisories=row.total or 0,
                critical_count=sev_map.get("CRITICAL", 0),
                high_count=sev_map.get("HIGH", 0),
                medium_count=sev_map.get("MEDIUM", 0),
            )
        )
    return results


@router.get(
    "/servers/{server_id}/vuln-summary",
    response_model=ServerVulnSummary,
    name="server_vuln_summary:get",
)
def get_server_vuln_summary(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerVulnSummary:
    """Return a detailed vulnerability exposure summary for a specific server,
    including per-severity counts and linked advisories.
    """
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

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

    return ServerVulnSummary(
        server_id=server_id,
        name=server.name,
        registry_source=server.registry_source,
        total_advisories=len(advisories),
        by_severity=by_severity,
        advisories=advisories,
    )


if __name__ == "__main__":
    # Self-test: fully standalone, uses LOCAL FastAPI() + dependency override.
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    from app.models import Base  # only needed at self-test time

    _eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=_eng)
    _TS = sessionmaker(bind=_eng, autoflush=False, autocommit=False)

    with _TS() as db:
        db.execute(text(
            "INSERT INTO mcp_server_registry (server_id, name, registry_source, url) "
            "VALUES ('srv1','Test Server 1','github','https://github.com/test/srv1'),"
            "('srv2','Test Server 2','npm','https://npmjs.com/srv2')"
        ))
        db.execute(text(
            "INSERT INTO vuln_advisories (id, feed, summary, severity, source_url, published_at) "
            "VALUES "
            "('CVE-2023-0001','nvd','Critical vuln','CRITICAL',"
            "'https://nvd.nist.gov/vuln/detail/CVE-2023-0001','2023-01-01T00:00:00'),"
            "('CVE-2023-0002','nvd','High vuln','HIGH',"
            "'https://nvd.nist.gov/vuln/detail/CVE-2023-0002','2023-01-02T00:00:00'),"
            "('CVE-2023-0003','ghsa','Medium vuln','MEDIUM',"
            "'https://github.com/advisories/CVE-2023-0003','2023-01-03T00:00:00')"
        ))
        db.execute(text(
            "INSERT INTO vuln_links (advisory_id, server_id, match_basis, match_value, match_confidence) "
            "VALUES "
            "('CVE-2023-0001','srv1','package_exact','pkg1',1.0),"
            "('CVE-2023-0002','srv1','package_exact','pkg1',1.0),"
            "('CVE-2023-0003','srv2','package_exact','pkg3',1.0)"
        ))
        db.commit()

    _that_app = FastAPI()
    _that_app.include_router(router)

    def _override_session():
        sess = _TS()
        try:
            yield sess
        finally:
            sess.close()

    _that_app.dependency_overrides[get_session] = _override_session
    c = TestClient(_that_app)

    resp = c.get("/api/servers/vuln-summary")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}: {resp.text}"
    payload = resp.json()
    assert isinstance(payload, list), f"Expected list, got {type(payload)}"
    assert len(payload) == 2, f"Expected 2 servers, got {len(payload)}"
    assert payload[0]["server_id"] == "srv1", "srv1 should be first (most advisories)"
    assert payload[0]["critical_count"] == 1
    assert payload[0]["high_count"] == 1

    resp = c.get("/api/servers/srv1/vuln-summary")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}: {resp.text}"
    detail = resp.json()
    assert detail["server_id"] == "srv1"
    assert detail["total_advisories"] == 2
    assert detail["by_severity"]["CRITICAL"] == 1
    assert detail["by_severity"]["HIGH"] == 1
    assert len(detail["advisories"]) == 2

    resp = c.get("/api/servers/nonexistent/vuln-summary")
    assert resp.status_code == 404

    print("PASS")
