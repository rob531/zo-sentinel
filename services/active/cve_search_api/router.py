from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Base, McpServerRegistry, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["cve_search_api"])


class AffectedServer(BaseModel):
    server_id: str
    server_name: str
    risk_tier: Optional[str]
    match_confidence: Optional[float]
    linked_at: Optional[datetime]


class CveSearchResponse(BaseModel):
    cve_id: str
    summary: Optional[str]
    severity: Optional[str]
    ecosystem: Optional[str]
    package: Optional[str]
    affected_servers: List[AffectedServer]


@router.get("/cve/search", response_model=List[CveSearchResponse])
def search_cve(
    cve_id: Optional[str] = Query(None, description="CVE identifier to search for"),
    session: Session = Depends(get_session),
) -> List[CveSearchResponse]:
    """
    Search for vulnerability advisories by CVE ID.
    Matches the CVE in VulnAdvisory.aliases (JSON array) and returns
    the advisory details plus all linked servers.
    """
    if not cve_id:
        return []

    # Use json_each to unnest the aliases JSON array and match the CVE ID.
    # Compatible with SQLite (JSON1) and PostgreSQL (json_array_elements).
    raw_sql = text("""
        SELECT DISTINCT
            va.id         AS adv_id,
            va.summary    AS adv_summary,
            va.severity   AS adv_severity,
            va.ecosystem  AS adv_ecosystem,
            va.package    AS adv_package,
            msr.server_id    AS srv_server_id,
            msr.name         AS srv_name,
            msr.risk_tier    AS srv_risk_tier,
            vl.match_confidence AS vl_match_conf,
            vl.linked_at     AS vl_linked_at
        FROM vuln_advisories va
        JOIN vuln_links vl ON vl.advisory_id = va.id
        LEFT JOIN mcp_server_registry msr ON msr.server_id = vl.server_id
        WHERE EXISTS (
            SELECT 1 FROM json_each(va.aliases) j
            WHERE j.value = :cve_id
        )
        ORDER BY va.id, vl.linked_at
    """)
    rows = session.execute(raw_sql, {"cve_id": cve_id}).fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No advisory found for CVE {cve_id}")

    # Group by advisory
    grouped: dict = {}
    for row in rows:
        key = (row.adv_id, row.adv_summary, row.adv_severity,
               row.adv_ecosystem, row.adv_package)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(AffectedServer(
            server_id=row.srv_server_id or "",
            server_name=row.srv_name or "",
            risk_tier=row.srv_risk_tier,
            match_confidence=float(row.vl_match_conf) if row.vl_match_conf is not None else None,
            linked_at=row.vl_linked_at,
        ))

    return [
        CveSearchResponse(
            cve_id=key[0],
            summary=key[1] or "",
            severity=key[2] or "",
            ecosystem=key[3] or "",
            package=key[4] or "",
            affected_servers=servers,
        )
        for key, servers in grouped.items()
    ]


if __name__ == "__main__":
    import os
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    # Seed servers
    for srv in [
        {"server_id": "srv-001", "name": "AlphaServer", "risk_tier": "critical"},
        {"server_id": "srv-002", "name": "BetaServer",  "risk_tier": "high"},
        {"server_id": "srv-003", "name": "GammaServer", "risk_tier": "medium"},
        {"server_id": "srv-004", "name": "DeltaServer", "risk_tier": "low"},
    ]:
        session.add(McpServerRegistry(**srv))
    session.commit()

    # Seed advisories with JSON aliases arrays
    for adv in [
        {"id": "CVE-2023-44487", "summary": "HTTP/2 Rapid Reset",
         "severity": "HIGH", "ecosystem": "pip", "package": "http-lib",
         "aliases": ["CVE-2023-44487", "CVE-2023-48023"]},
        {"id": "CVE-2023-48023", "summary": "Related protocol vuln",
         "severity": "CRITICAL", "ecosystem": "npm", "package": "net-stack",
         "aliases": ["CVE-2023-48023"]},
        {"id": "CVE-2024-0001", "summary": "Test CVE unrelated",
         "severity": "MEDIUM", "ecosystem": "pypi", "package": "test-pkg",
         "aliases": ["CVE-2024-0001"]},
    ]:
        session.add(VulnAdvisory(**adv))
    session.commit()

    now = datetime.utcnow()
    for vl in [
        {"advisory_id": "CVE-2023-44487", "server_id": "srv-001",
         "match_confidence": 0.95, "linked_at": now},
        {"advisory_id": "CVE-2023-44487", "server_id": "srv-002",
         "match_confidence": 0.88, "linked_at": now},
        {"advisory_id": "CVE-2023-44487", "server_id": "srv-003",
         "match_confidence": 0.72, "linked_at": now},
        {"advisory_id": "CVE-2023-48023", "server_id": "srv-002",
         "match_confidence": 0.91, "linked_at": now},
        {"advisory_id": "CVE-2024-0001",  "server_id": "srv-004",
         "match_confidence": 0.55, "linked_at": now},
    ]:
        session.add(VulnLink(**vl))
    session.commit()

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(app)

    # Test 1: search CVE-2023-44487 (in aliases of 2 advisories)
    resp = client.get("/api/cve/search?cve_id=CVE-2023-44487")
    assert resp.status_code == 200, f"CVE-2023-44487: expected 200, got {resp.status_code}"
    data = resp.json()
    assert len(data) == 1, f"CVE-2023-44487: expected 1 advisory, got {len(data)}"
    assert data[0]["cve_id"] == "CVE-2023-44487"
    assert len(data[0]["affected_servers"]) == 3, f"Expected 3 servers, got {len(data[0]['affected_servers'])}"

    # Test 2: search CVE-2023-48023 (aliased in CVE-2023-44487 too)
    resp2 = client.get("/api/cve/search?cve_id=CVE-2023-48023")
    assert resp2.status_code == 200, f"CVE-2023-48023: expected 200, got {resp2.status_code}"
    data2 = resp2.json()
    assert len(data2) == 2, f"CVE-2023-48023: expected 2 advisories, got {len(data2)}"

    # Test 3: unknown CVE
    resp3 = client.get("/api/cve/search?cve_id=CVE-9999-9999")
    assert resp3.status_code == 404, f"Unknown CVE: expected 404, got {resp3.status_code}"

    # Test 4: empty cve_id
    resp4 = client.get("/api/cve/search")
    assert resp4.status_code == 200, f"Empty CVE: expected 200, got {resp4.status_code}"
    assert resp4.json() == [], f"Empty CVE: expected [], got {resp4.json()}"

    print("PASS")
