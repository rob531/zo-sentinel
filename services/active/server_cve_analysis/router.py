# deps: fastapi, pydantic, sqlalchemy, requests
"""Server CVE Analysis Service.

Provides CVE risk analysis for servers: composite exposure scores, severity
breakdowns, trend indicators, server comparison, and ranked listings.
Reads from app Postgres (McpServerRegistry, VulnAdvisory, VulnLink) via get_session.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["server_cve_analysis"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class SeverityCount(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    unknown: int = 0


class ServerCveAnalysisDetail(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    exposure_score: float
    severity_breakdown: SeverityCount
    total_advisories: int
    total_links: int
    feeds: List[str]
    ecosystems: List[str]
    latest_advisory_date: Optional[str] = None
    trend_indicator: str
    top_advisories: List[Dict]


class ServerCveComparisonItem(BaseModel):
    server_id: str
    name: Optional[str] = None
    exposure_score: float
    total_advisories: int
    critical_count: int
    high_count: int


class CveAnalysisComparison(BaseModel):
    server_ids: List[str]
    servers: List[ServerCveComparisonItem]
    shared_cves: List[str]


class CveAnalysisSummary(BaseModel):
    total_servers: int
    servers_with_cves: int
    total_advisories: int
    total_links: int
    avg_exposure_score: float
    by_severity: Dict[str, int]
    by_feed: Dict[str, int]
    by_ecosystem: Dict[str, int]


class CveAnalysisRankItem(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    exposure_score: float
    total_advisories: int
    critical_count: int
    high_count: int


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

SEVERITY_WEIGHTS = {
    "CRITICAL": 10.0,
    "HIGH": 5.0,
    "MEDIUM": 2.0,
    "LOW": 0.5,
    "UNKNOWN": 0.0,
}


def _compute_exposure_score(sev_counts: Dict[str, int]) -> float:
    raw = sum(SEVERITY_WEIGHTS.get(sev, 0) * count for sev, count in sev_counts.items())
    score = min(raw * 10, 100.0)
    return round(score, 2)


def _severity_count_from_dict(sev_map: Dict[str, int]) -> SeverityCount:
    return SeverityCount(
        critical=sev_map.get("CRITICAL", 0),
        high=sev_map.get("HIGH", 0),
        medium=sev_map.get("MEDIUM", 0),
        low=sev_map.get("LOW", 0),
        unknown=sev_map.get("UNKNOWN", 0),
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/cve-analysis/summary",
    response_model=CveAnalysisSummary,
    name="server_cve_analysis:summary",
)
def get_analysis_summary(
    days: int = Query(default=90, ge=1, le=730),
    db: Session = Depends(get_session),
) -> CveAnalysisSummary:
    """Return aggregate CVE analysis statistics across all servers."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    total_advisories = db.query(func.count(VulnAdvisory.id)).scalar() or 0
    total_links = db.query(func.count(VulnLink.id)).scalar() or 0
    servers_with_cves = (
        db.query(func.count(func.distinct(VulnLink.server_id)))
        .filter(VulnLink.advisory_id.isnot(None))
        .scalar()
        or 0
    )
    total_servers = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    sev_rows = (
        db.query(VulnAdvisory.severity, func.count(VulnAdvisory.id))
        .filter(VulnAdvisory.published_at >= cutoff)
        .group_by(VulnAdvisory.severity)
        .all()
    )
    by_severity: Dict[str, int] = {r.severity or "UNKNOWN": r[1] for r in sev_rows}

    feed_rows = (
        db.query(VulnAdvisory.feed, func.count(VulnAdvisory.id))
        .filter(VulnAdvisory.published_at >= cutoff)
        .group_by(VulnAdvisory.feed)
        .all()
    )
    by_feed = {r.feed or "UNKNOWN": r[1] for r in feed_rows}

    eco_rows = (
        db.query(VulnAdvisory.ecosystem, func.count(VulnAdvisory.id))
        .filter(
            VulnAdvisory.published_at >= cutoff,
            VulnAdvisory.ecosystem.isnot(None),
        )
        .group_by(VulnAdvisory.ecosystem)
        .all()
    )
    by_ecosystem = {r.ecosystem: r[1] for r in eco_rows}

    avg_score = _compute_exposure_score(by_severity)

    return CveAnalysisSummary(
        total_servers=total_servers,
        servers_with_cves=servers_with_cves,
        total_advisories=total_advisories,
        total_links=total_links,
        avg_exposure_score=avg_score,
        by_severity=by_severity,
        by_feed=by_feed,
        by_ecosystem=by_ecosystem,
    )


@router.get(
    "/servers/{server_id}/cve-analysis",
    response_model=ServerCveAnalysisDetail,
    name="server_cve_analysis:get",
)
def get_server_cve_analysis(
    server_id: str,
    days: int = Query(default=90, ge=1, le=730),
    db: Session = Depends(get_session),
) -> ServerCveAnalysisDetail:
    """Return detailed CVE risk analysis for a specific server."""
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    sev_rows = (
        db.query(VulnAdvisory.severity, func.count(VulnAdvisory.id))
        .join(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
        .filter(
            VulnLink.server_id == server_id,
            VulnAdvisory.published_at >= cutoff,
        )
        .group_by(VulnAdvisory.severity)
        .all()
    )
    sev_map: Dict[str, int] = {r.severity or "UNKNOWN": r[1] for r in sev_rows}

    total_advisories = sum(sev_map.values())
    total_links = (
        db.query(func.count(VulnLink.id))
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .filter(
            VulnLink.server_id == server_id,
            VulnAdvisory.published_at >= cutoff,
        )
        .scalar()
        or 0
    )

    exposure_score = _compute_exposure_score(sev_map)

    feed_rows = (
        db.query(func.distinct(VulnAdvisory.feed))
        .join(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
        .filter(
            VulnLink.server_id == server_id,
            VulnAdvisory.published_at >= cutoff,
            VulnAdvisory.feed.isnot(None),
        )
        .all()
    )
    feeds = [r[0] for r in feed_rows]

    eco_rows = (
        db.query(func.distinct(VulnAdvisory.ecosystem))
        .join(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
        .filter(
            VulnLink.server_id == server_id,
            VulnAdvisory.published_at >= cutoff,
            VulnAdvisory.ecosystem.isnot(None),
        )
        .all()
    )
    ecosystems = [r[0] for r in eco_rows]

    latest_date_row = (
        db.query(func.max(VulnAdvisory.published_at))
        .join(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
        .filter(
            VulnLink.server_id == server_id,
            VulnAdvisory.published_at >= cutoff,
        )
        .scalar()
    )
    latest_date_str = None
    if latest_date_row:
        latest_date_str = (
            latest_date_row.isoformat()
            if isinstance(latest_date_row, datetime)
            else str(latest_date_row)
        )

    links_q = (
        db.query(VulnLink, VulnAdvisory)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .filter(
            VulnLink.server_id == server_id,
            VulnAdvisory.published_at >= cutoff,
        )
        .order_by(VulnAdvisory.published_at.desc())
        .all()
    )
    top_advisories = [
        {
            "id": adv.id,
            "summary": adv.summary,
            "severity": adv.severity,
            "feed": adv.feed,
            "ecosystem": adv.ecosystem,
            "package": adv.package,
            "source_url": adv.source_url,
            "published_at": (
                adv.published_at.isoformat()
                if isinstance(adv.published_at, datetime)
                else str(adv.published_at) if adv.published_at else None
            ),
            "match_confidence": float(link.match_confidence) if link.match_confidence else None,
        }
        for link, adv in links_q
    ]

    trend_indicator = "worsening" if sev_map.get("CRITICAL", 0) > 0 else "stable"
    if sev_map.get("CRITICAL", 0) == 0 and sev_map.get("HIGH", 0) == 0:
        trend_indicator = "improving"

    return ServerCveAnalysisDetail(
        server_id=server_id,
        name=server.name,
        registry_source=server.registry_source,
        risk_tier=server.risk_tier,
        exposure_score=exposure_score,
        severity_breakdown=_severity_count_from_dict(sev_map),
        total_advisories=total_advisories,
        total_links=total_links,
        feeds=feeds,
        ecosystems=ecosystems,
        latest_advisory_date=latest_date_str,
        trend_indicator=trend_indicator,
        top_advisories=top_advisories,
    )


@router.get(
    "/servers/cve-analysis/ranked",
    response_model=List[CveAnalysisRankItem],
    name="server_cve_analysis:ranked",
)
def rank_servers_by_cve_risk(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    days: int = Query(default=90, ge=1, le=730),
    min_severity: Optional[str] = Query(
        None, description="Minimum severity to count (CRITICAL, HIGH, MEDIUM)"
    ),
    db: Session = Depends(get_session),
) -> List[CveAnalysisRankItem]:
    """Return servers ranked by CVE exposure score (descending)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    server_ids_rows = (
        db.query(func.distinct(VulnLink.server_id))
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .filter(VulnAdvisory.published_at >= cutoff)
        .offset(skip)
        .limit(limit)
        .all()
    )
    server_ids = [r[0] for r in server_ids_rows]

    results: List[CveAnalysisRankItem] = []
    for sid in server_ids:
        srv = db.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == sid
        ).first()
        if not srv:
            continue

        sev_rows = (
            db.query(VulnAdvisory.severity, func.count(VulnAdvisory.id))
            .join(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
            .filter(
                VulnLink.server_id == sid,
                VulnAdvisory.published_at >= cutoff,
            )
            .group_by(VulnAdvisory.severity)
            .all()
        )
        sev_map = {r.severity or "UNKNOWN": r[1] for r in sev_rows}

        if min_severity:
            threshold_severities = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1}
            min_level = threshold_severities.get(min_severity, 0)
            active_level = threshold_severities.get(
                next((s for s in ["CRITICAL", "HIGH", "MEDIUM", "LOW"] if sev_map.get(s, 0) > 0), "UNKNOWN"),
                -1,
            )
            if active_level < min_level:
                continue

        score = _compute_exposure_score(sev_map)
        results.append(
            CveAnalysisRankItem(
                server_id=sid,
                name=srv.name,
                registry_source=srv.registry_source,
                risk_tier=srv.risk_tier,
                exposure_score=score,
                total_advisories=sum(sev_map.values()),
                critical_count=sev_map.get("CRITICAL", 0),
                high_count=sev_map.get("HIGH", 0),
            )
        )

    results.sort(key=lambda x: -x.exposure_score)
    return results


@router.post(
    "/servers/cve-analysis/compare",
    response_model=CveAnalysisComparison,
    name="server_cve_analysis:compare",
)
def compare_servers_cve_analysis(
    server_ids: List[str],
    days: int = Query(default=90, ge=1, le=730),
    db: Session = Depends(get_session),
) -> CveAnalysisComparison:
    """Compare CVE exposure across multiple servers."""
    if not server_ids:
        raise HTTPException(status_code=400, detail="server_ids cannot be empty")
    if len(server_ids) > 20:
        raise HTTPException(status_code=400, detail="server_ids cannot exceed 20")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    servers_out: List[ServerCveComparisonItem] = []

    for sid in server_ids:
        srv = db.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == sid
        ).first()

        sev_rows = (
            db.query(VulnAdvisory.severity, func.count(VulnAdvisory.id))
            .join(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
            .filter(
                VulnLink.server_id == sid,
                VulnAdvisory.published_at >= cutoff,
            )
            .group_by(VulnAdvisory.severity)
            .all()
        )
        sev_map = {r.severity or "UNKNOWN": r[1] for r in sev_rows}
        score = _compute_exposure_score(sev_map)
        total = sum(sev_map.values())
        servers_out.append(
            ServerCveComparisonItem(
                server_id=sid,
                name=srv.name if srv else None,
                exposure_score=score,
                total_advisories=total,
                critical_count=sev_map.get("CRITICAL", 0),
                high_count=sev_map.get("HIGH", 0),
            )
        )

    shared_cves: List[str] = []
    if len(server_ids) > 1:
        advisory_sets = []
        for sid in server_ids:
            adv_ids = set(
                r[0]
                for r in (
                    db.query(func.distinct(VulnLink.advisory_id))
                    .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
                    .filter(
                        VulnLink.server_id == sid,
                        VulnAdvisory.published_at >= cutoff,
                    )
                    .all()
                )
            )
            advisory_sets.append(adv_ids)
        shared_cves = list(set.intersection(*advisory_sets))[:50]

    return CveAnalysisComparison(
        server_ids=server_ids,
        servers=servers_out,
        shared_cves=shared_cves,
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

    # Test summary
    resp = _c.get("/api/cve-analysis/summary")
    assert resp.status_code == 200, f"Summary failed: {resp.status_code} {resp.text}"
    summary = resp.json()
    assert summary["total_advisories"] == 4
    assert summary["total_links"] == 5
    assert "by_feed" in summary
    assert "by_ecosystem" in summary

    # Test detail for srv1 (default 90-day window excludes CVE-2022)
    resp = _c.get("/api/servers/srv1/cve-analysis")
    assert resp.status_code == 200, f"Detail failed: {resp.status_code} {resp.text}"
    detail = resp.json()
    assert detail["server_id"] == "srv1"
    assert detail["total_advisories"] == 2, f"Expected 2, got {detail['total_advisories']}"
    assert detail["severity_breakdown"]["critical"] == 1
    assert detail["severity_breakdown"]["high"] == 1
    assert detail["exposure_score"] == 150.0
    assert detail["trend_indicator"] == "worsening"
    assert "nvd" in detail["feeds"]
    assert "npm" in detail["ecosystems"]

    # Test srv2 (only MEDIUM)
    resp = _c.get("/api/servers/srv2/cve-analysis")
    assert resp.status_code == 200
    detail2 = resp.json()
    assert detail2["total_advisories"] == 1
    assert detail2["severity_breakdown"]["medium"] == 1
    assert detail2["trend_indicator"] == "stable"

    # Test srv3 (no CVEs)
    resp = _c.get("/api/servers/srv3/cve-analysis")
    assert resp.status_code == 200
    detail3 = resp.json()
    assert detail3["total_advisories"] == 0
    assert detail3["exposure_score"] == 0.0
    assert detail3["trend_indicator"] == "improving"

    # Test with wider window to include CVE-2022
    resp = _c.get("/api/servers/srv1/cve-analysis?days=500")
    assert resp.status_code == 200
    assert resp.json()["total_advisories"] == 3

    # Test 404
    resp = _c.get("/api/servers/nonexistent/cve-analysis")
    assert resp.status_code == 404

    # Test ranked
    resp = _c.get("/api/servers/cve-analysis/ranked")
    assert resp.status_code == 200
    ranked = resp.json()
    assert isinstance(ranked, list)
    assert len(ranked) == 2
    assert ranked[0]["server_id"] == "srv1"
    assert ranked[0]["exposure_score"] == 150.0
    assert ranked[1]["server_id"] == "srv2"

    # Test ranked with min_severity filter
    resp = _c.get("/api/servers/cve-analysis/ranked?min_severity=HIGH")
    assert resp.status_code == 200
    ranked_h = resp.json()
    assert len(ranked_h) == 1
    assert ranked_h[0]["server_id"] == "srv1"

    # Test compare
    resp = _c.post("/api/servers/cve-analysis/compare?days=90", json=["srv1", "srv2"])
    assert resp.status_code == 200, f"Compare failed: {resp.status_code} {resp.text}"
    comp = resp.json()
    assert comp["server_ids"] == ["srv1", "srv2"]
    assert len(comp["servers"]) == 2
    assert comp["shared_cves"] == ["CVE-2023-0001"]

    # Test compare empty
    resp = _c.post("/api/servers/cve-analysis/compare", json=[])
    assert resp.status_code == 400

    # Test compare > 20
    resp = _c.post("/api/servers/cve-analysis/compare", json=[f"srv{i}" for i in range(25)])
    assert resp.status_code == 400

    print("PASS")
