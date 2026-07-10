"""threat_intel_summary_api.py -- Aggregated threat intelligence per server.

Reads from three threat tables:
  - threat_intel_refs: pulse count + sources (matched by indicator_value vs registry URL/name)
  - vuln_links JOIN vuln_advisories: advisories by severity
  - vuln_links: direct linkage count

Output: {server_id, threat_refs, vuln_advisories, overall_threat_level, fetched_at}
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, or_, and_, distinct
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, ThreatIntelRef, VulnLink, VulnAdvisory

router = APIRouter(prefix="/servers", tags=["threat-intel"])


class ThreatRefs(BaseModel):
    total_refs: int
    pulses: int
    sources: list[str]


class VulnAdvisoriesSummary(BaseModel):
    total: int
    by_severity: Dict[Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"], int]
    osv_count: int


class ThreatIntelSummary(BaseModel):
    server_id: str
    threat_refs: ThreatRefs
    vuln_advisories: VulnAdvisoriesSummary
    overall_threat_level: Literal["none", "low", "medium", "high", "critical"]
    fetched_at: str


THREAT_LEVEL_ORDER = ("none", "low", "medium", "high", "critical")


def _compute_overall_threat(sev_counts: dict, total_refs: int) -> str:
    """Derive overall threat level from advisory severity counts + pulse refs."""
    if total_refs == 0 and sev_counts.get("CRITICAL", 0) == 0 and sev_counts.get("HIGH", 0) == 0 \
            and sev_counts.get("MEDIUM", 0) == 0 and sev_counts.get("LOW", 0) == 0 \
            and sev_counts.get("UNKNOWN", 0) == 0:
        return "none"
    if sev_counts.get("CRITICAL", 0) > 0:
        return "critical"
    if sev_counts.get("HIGH", 0) > 0:
        return "high"
    if sev_counts.get("MEDIUM", 0) > 0:
        return "medium"
    if sev_counts.get("LOW", 0) > 0:
        return "low"
    if total_refs > 0:
        return "low"
    return "none"


@router.get("/{server_id}/threat-intel-summary", response_model=ThreatIntelSummary)
def threat_intel_summary(server_id: str, db: Session = Depends(get_session)) -> ThreatIntelSummary:
    """Aggregated threat intelligence for a server across all three threat tables."""
    reg = db.get(McpServerRegistry, server_id)
    if reg is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id!r} not found")

    # ---- threat_intel_refs: match by indicator_value against registry URL + name ----
    indicators: list[str] = []
    if reg.url:
        indicators.append(reg.url)
        domain = reg.url.split("://", 1)[-1].rstrip("/")
        if domain:
            indicators.append(domain)
            parts = domain.split("/")
            if parts:
                indicators.append(parts[0])
    if reg.name:
        indicators.append(reg.name)

    # Build query: always filter on indicator_value.in_() — empty list returns no rows
    threat_refs_query = db.execute(
        select(ThreatIntelRef).where(ThreatIntelRef.indicator_value.in_(indicators))
    ).scalars().all() if indicators else []

    total_refs = len(threat_refs_query)
    pulse_ids = set(r.pulse_id for r in threat_refs_query if r.pulse_id)
    pulses = len(pulse_ids)
    sources = sorted(set(r.source for r in threat_refs_query if r.source))

    # ---- vuln_links + vuln_advisories: count by server_id ----
    vuln_rows = db.execute(
        select(VulnLink).where(VulnLink.server_id == server_id)
    ).scalars().all()

    advisory_ids = [r.advisory_id for r in vuln_rows if r.advisory_id]
    sev_counts: Dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
    osv_count = 0
    total_vulns = len(vuln_rows)

    if advisory_ids:
        advisories = db.execute(
            select(VulnAdvisory).where(VulnAdvisory.id.in_(advisory_ids))
        ).scalars().all()
        for adv in advisories:
            sev = (adv.severity or "UNKNOWN").upper()
            sev_counts[sev] = sev_counts.get(sev, 0) + 1
            if adv.feed == "osv":
                osv_count += 1

    overall_threat_level = _compute_overall_threat(sev_counts, total_refs)

    return ThreatIntelSummary(
        server_id=server_id,
        threat_refs=ThreatRefs(total_refs=total_refs, pulses=pulses, sources=sources),
        vuln_advisories=VulnAdvisoriesSummary(
            total=total_vulns,
            by_severity=sev_counts,
            osv_count=osv_count,
        ),
        overall_threat_level=overall_threat_level,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()

    # Seed registry entry
    s.add(McpServerRegistry(
        server_id="test-server",
        name="Test MCP Server",
        url="https://github.com/example/test-mcp"
    ))

    # Seed threat intel refs (matched by URL)
    s.add(ThreatIntelRef(
        indicator_type="domain",
        indicator_value="https://github.com/example/test-mcp",
        pulse_id="pulse-001",
        pulse_name="Test Pulse 1",
        source="otx",
        source_url="https://otx.alienvault.com/pulse/pulse-001"
    ))
    s.add(ThreatIntelRef(
        indicator_type="domain",
        indicator_value="github.com",
        pulse_id="pulse-002",
        pulse_name="Aggregator Pulse",
        is_aggregator=True,
        source="otx",
        source_url="https://otx.alienvault.com/pulse/pulse-002"
    ))

    # Seed vuln advisories
    s.add(VulnAdvisory(
        id="CVE-2024-0001",
        feed="osv",
        summary="Critical test vuln",
        severity="CRITICAL",
        ecosystem="pip",
        package="test-package",
        source_url="https://osv.dev/vulnerability/CVE-2024-0001"
    ))
    s.add(VulnAdvisory(
        id="GHSA-2024-0002",
        feed="ghsa",
        summary="High test vuln",
        severity="HIGH",
        ecosystem="npm",
        package="@example/test-lib",
        source_url="https://github.com/advisories/GHSA-2024-0002"
    ))

    # Seed vuln links
    s.add(VulnLink(
        advisory_id="CVE-2024-0001",
        server_id="test-server",
        match_basis="repo_exact",
        match_value="github.com/example/test-mcp",
        match_confidence=1.0
    ))
    s.add(VulnLink(
        advisory_id="GHSA-2024-0002",
        server_id="test-server",
        match_basis="package_exact",
        match_value="@example/test-lib",
        match_confidence=1.0
    ))

    # Seed a server with no threat data (use a domain with no matching ThreatIntelRef)
    s.add(McpServerRegistry(
        server_id="clean-server",
        name="Clean Server",
        url="https://gitlab.com/clean/server"
    ))

    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    # Happy path: server with threat intel
    r = c.get("/servers/test-server/threat-intel-summary")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["server_id"] == "test-server"
    assert "threat_refs" in j
    assert j["threat_refs"]["total_refs"] == 2
    assert j["threat_refs"]["pulses"] == 2
    assert "otx" in j["threat_refs"]["sources"]
    assert "vuln_advisories" in j
    assert j["vuln_advisories"]["total"] == 2
    assert "CRITICAL" in j["vuln_advisories"]["by_severity"]
    assert "HIGH" in j["vuln_advisories"]["by_severity"]
    assert j["vuln_advisories"]["osv_count"] == 1
    assert j["overall_threat_level"] in THREAT_LEVEL_ORDER
    assert j["overall_threat_level"] == "critical"  # has CRITICAL advisory
    assert "fetched_at" in j

    # Edge case: server with no threat data
    r2 = c.get("/servers/clean-server/threat-intel-summary")
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    # Debug
    print(f"DEBUG clean-server response: {j2}")
    print(f"DEBUG sev_counts: total_refs=0, CRITICAL=0, HIGH=0, MEDIUM=0, LOW=0, UNKNOWN=0")
    assert j2["overall_threat_level"] in ("none",), f"got {j2['overall_threat_level']!r}"
    assert j2["threat_refs"]["total_refs"] == 0
    assert j2["vuln_advisories"]["total"] == 0

    # 404 case: unknown server
    r3 = c.get("/servers/nonexistent-server/threat-intel-summary")
    assert r3.status_code == 404, r3.text

    print("PASS")
