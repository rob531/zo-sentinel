# deps: fastapi, pydantic, sqlalchemy
"""CVE Detail API – public endpoints to look up a CVE by ID and retrieve
full advisory details plus linked MCP servers."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from fastapi import APIRouter, Depends, HTTPException, Path as FastAPIPath, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["cve_detail_api"])


# ── Response models ──────────────────────────────────────────────────────────

class LinkedServer(BaseModel):
    server_id: str
    name: Optional[str]
    registry_source: Optional[str]
    risk_tier: Optional[str]
    match_basis: Optional[str]
    match_value: Optional[str]
    match_confidence: Optional[float]
    linked_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class CveDetailResponse(BaseModel):
    id: str
    feed: Optional[str]
    summary: Optional[str]
    severity: Optional[str]
    ecosystem: Optional[str]
    package: Optional[str]
    affected_ranges: Optional[dict]
    aliases: Optional[List[str]]
    source_url: Optional[str]
    published_at: Optional[datetime]
    fetched_at: Optional[datetime]
    identities: Optional[dict]
    linked_servers: List[LinkedServer] = []
    linked_server_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class CveListResponse(BaseModel):
    id: str
    feed: Optional[str]
    summary: Optional[str]
    severity: Optional[str]
    ecosystem: Optional[str]
    package: Optional[str]
    published_at: Optional[datetime]
    linked_server_count: int = 0


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get(
    "/cve/{cve_id}",
    response_model=CveDetailResponse,
    summary="Get CVE detail by ID",
)
def get_cve_detail(
    cve_id: str = FastAPIPath(..., description="CVE identifier (e.g. CVE-2023-44487)"),
    db: Session = Depends(get_session),
) -> CveDetailResponse:
    """Return full detail for one CVE, including all linked MCP servers."""
    advisory = (
        db.query(VulnAdvisory)
        .filter(VulnAdvisory.id == cve_id)
        .first()
    )
    if not advisory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CVE '{cve_id}' not found",
        )

    links = (
        db.query(VulnLink, McpServerRegistry)
        .outerjoin(McpServerRegistry, VulnLink.server_id == McpServerRegistry.server_id)
        .filter(VulnLink.advisory_id == cve_id)
        .all()
    )

    linked_servers = [
        LinkedServer(
            server_id=link.server_id,
            name=srv.name if srv else None,
            registry_source=srv.registry_source if srv else None,
            risk_tier=srv.risk_tier if srv else None,
            match_basis=link.match_basis,
            match_value=link.match_value,
            match_confidence=link.match_confidence,
            linked_at=link.linked_at,
        )
        for link, srv in links
    ]

    return CveDetailResponse(
        id=advisory.id,
        feed=advisory.feed,
        summary=advisory.summary,
        severity=advisory.severity,
        ecosystem=advisory.ecosystem,
        package=advisory.package,
        affected_ranges=advisory.affected_ranges,
        aliases=advisory.aliases,
        source_url=advisory.source_url,
        published_at=advisory.published_at,
        fetched_at=advisory.fetched_at,
        identities=advisory.identities,
        linked_servers=linked_servers,
        linked_server_count=len(linked_servers),
    )


@router.get(
    "/cve/{cve_id}/servers",
    response_model=List[LinkedServer],
    summary="List servers linked to a CVE",
)
def get_cve_servers(
    cve_id: str = FastAPIPath(..., description="CVE identifier"),
    db: Session = Depends(get_session),
) -> List[LinkedServer]:
    """Return all MCP servers linked to the given CVE."""
    advisory = (
        db.query(VulnAdvisory)
        .filter(VulnAdvisory.id == cve_id)
        .first()
    )
    if not advisory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CVE '{cve_id}' not found",
        )

    links = (
        db.query(VulnLink, McpServerRegistry)
        .outerjoin(McpServerRegistry, VulnLink.server_id == McpServerRegistry.server_id)
        .filter(VulnLink.advisory_id == cve_id)
        .all()
    )

    return [
        LinkedServer(
            server_id=link.server_id,
            name=srv.name if srv else None,
            registry_source=srv.registry_source if srv else None,
            risk_tier=srv.risk_tier if srv else None,
            match_basis=link.match_basis,
            match_value=link.match_value,
            match_confidence=link.match_confidence,
            linked_at=link.linked_at,
        )
        for link, srv in links
    ]


@router.get(
    "/cve",
    response_model=List[CveListResponse],
    summary="List CVEs with server linkage counts",
)
def list_cves(
    limit: int = 50,
    db: Session = Depends(get_session),
) -> List[CveListResponse]:
    """Return a flat list of CVEs with linked-server counts, ordered by recency."""
    rows = (
        db.query(
            VulnAdvisory,
            func.count(VulnLink.server_id).label("link_cnt"),
        )
        .outerjoin(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
        .group_by(VulnAdvisory.id)
        .order_by(VulnAdvisory.published_at.desc().nullslast())
        .limit(limit)
        .all()
    )

    return [
        CveListResponse(
            id=adv.id,
            feed=adv.feed,
            summary=adv.summary,
            severity=adv.severity,
            ecosystem=adv.ecosystem,
            package=adv.package,
            published_at=adv.published_at,
            linked_server_count=link_cnt,
        )
        for adv, link_cnt in rows
    ]


# ── Self-test ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    # Seed test data
    with TestSession() as db:
        db.add_all([
            McpServerRegistry(
                server_id="srv-001",
                name="AlphaServer",
                registry_source="github",
                risk_tier="high",
            ),
            McpServerRegistry(
                server_id="srv-002",
                name="BetaServer",
                registry_source="npm",
                risk_tier="medium",
            ),
        ])
        db.add_all([
            VulnAdvisory(
                id="CVE-2023-44487",
                feed="nvd",
                summary="HTTP/2 Rapid Reset Attack",
                severity="HIGH",
                ecosystem="pip",
                package="http-lib",
                affected_ranges={"ranges": []},
                aliases=["CVE-2023-44487"],
                source_url="https://nvd.nist.gov/vuln/detail/CVE-2023-44487",
                published_at=datetime(2023, 10, 10, 0, 0, 0),
                fetched_at=datetime.utcnow(),
                identities={},
            ),
            VulnAdvisory(
                id="CVE-2023-48023",
                feed="ghsa",
                summary="Related protocol issue",
                severity="CRITICAL",
                ecosystem="npm",
                package="net-stack",
                affected_ranges={"ranges": []},
                aliases=["CVE-2023-48023"],
                source_url="https://github.com/advisories/CVE-2023-48023",
                published_at=datetime(2023, 11, 1, 0, 0, 0),
                fetched_at=datetime.utcnow(),
                identities={},
            ),
        ])
        now = datetime.utcnow()
        db.add_all([
            VulnLink(
                advisory_id="CVE-2023-44487",
                server_id="srv-001",
                match_basis="package_exact",
                match_value="http-lib",
                match_confidence=0.95,
                linked_at=now,
            ),
            VulnLink(
                advisory_id="CVE-2023-44487",
                server_id="srv-002",
                match_basis="package_prefix",
                match_value="net-",
                match_confidence=0.72,
                linked_at=now,
            ),
            VulnLink(
                advisory_id="CVE-2023-48023",
                server_id="srv-002",
                match_basis="package_exact",
                match_value="net-stack",
                match_confidence=0.88,
                linked_at=now,
            ),
        ])
        db.commit()

    # Test 1: GET /api/cve/{id} – full detail
    r = client.get("/api/cve/CVE-2023-44487")
    assert r.status_code == 200, f"detail 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["id"] == "CVE-2023-44487"
    assert body["severity"] == "HIGH"
    assert body["linked_server_count"] == 2
    assert len(body["linked_servers"]) == 2
    assert body["linked_servers"][0]["server_id"] in ("srv-001", "srv-002")

    # Test 2: GET /api/cve/{id}/servers – linked servers only
    r = client.get("/api/cve/CVE-2023-44487/servers")
    assert r.status_code == 200, f"servers 200, got {r.status_code}"
    assert isinstance(r.json(), list)
    assert len(r.json()) == 2

    # Test 3: GET /api/cve – list endpoint
    r = client.get("/api/cve")
    assert r.status_code == 200, f"list 200, got {r.status_code}"
    items = r.json()
    assert isinstance(items, list)
    assert len(items) == 2
    # ordered by published_at desc
    assert items[0]["id"] == "CVE-2023-48023"  # more recent
    assert items[0]["linked_server_count"] == 1
    assert items[1]["id"] == "CVE-2023-44487"
    assert items[1]["linked_server_count"] == 2

    # Test 4: 404 for unknown CVE
    r = client.get("/api/cve/CVE-9999-99999")
    assert r.status_code == 404, f"404 expected, got {r.status_code}"

    # Test 5: 404 for unknown CVE servers endpoint
    r = client.get("/api/cve/CVE-9999-99999/servers")
    assert r.status_code == 404

    print("PASS")
