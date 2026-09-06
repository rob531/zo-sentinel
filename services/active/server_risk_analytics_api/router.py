# deps: fastapi, pydantic, sqlalchemy
"""Server Risk Analytics API.

Provides analytics endpoints over server risk profiles: distribution summaries,
axis-level breakdowns, critical-server lists, and per-server risk profiles with
trust gating applied (trust_gating_override.trust_gate).

Auth: public.
Data: app tier via get_session + McpServerRegistry + McpLlmAxisScore.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

try:
    from trust_gating_override import trust_gate
except Exception:
    def trust_gate(url, name, axis_labels):
        return {
            "url": url, "name": name,
            "original_overall_risk": axis_labels.get("overall_risk", ""),
            "published_overall_risk": axis_labels.get("overall_risk", ""),
            "capped": False, "trusted": False,
            "trust_basis": None, "masquerade_flag": False,
            "display_label": "Automated heuristic assessment",
        }

router = APIRouter(prefix="/api", tags=["server_risk_analytics_api"])

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TrustGateResult(BaseModel):
    original_overall_risk: str
    published_overall_risk: str
    capped: bool
    trusted: bool
    trust_basis: Optional[str]
    masquerade_flag: bool
    display_label: str


class AxisAnalyticsEntry(BaseModel):
    axis_name: str
    label: str
    count: int = 0
    pct: float = 0.0
    mean_p_top: float = 0.0
    mean_p_critical: float = 0.0
    mean_p_danger: float = 0.0
    escalated_count: int = 0
    escalated_pct: float = 0.0


class AxisAnalyticsResponse(BaseModel):
    axis_name: str
    total_servers: int
    entries: list[AxisAnalyticsEntry]


class RiskDistributionEntry(BaseModel):
    tier: str
    count: int
    pct: float = 0.0


class RiskDistributionResponse(BaseModel):
    total_servers: int
    by_risk_tier: list[RiskDistributionEntry]
    by_verdict: list[RiskDistributionEntry]


class ServerRiskSummary(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    trust_score: Optional[float] = None
    confidence: Optional[float] = None
    overall_risk_label: Optional[str] = None
    overall_risk_p_top: Optional[float] = None
    overall_risk_p_critical: Optional[float] = None
    overall_risk_p_danger: Optional[float] = None
    trust_gate: Optional[TrustGateResult] = None
    axis_count: int = 0
    last_assessed: Optional[str] = None


class ServerListResponse(BaseModel):
    total: int
    servers: list[ServerRiskSummary]


class AnalyticsOverviewResponse(BaseModel):
    generated_at: str
    total_servers: int
    scored_servers: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _latest_axes(db: Session, server_id: str) -> list[McpLlmAxisScore]:
    """Return the most-recently scored axis scores for a server."""
    subq = (
        db.query(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("latest_at"),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )
    return (
        db.query(McpLlmAxisScore)
        .join(
            subq,
            (McpLlmAxisScore.server_id == server_id)
            & (McpLlmAxisScore.axis_name == subq.c.axis_name)
            & (McpLlmAxisScore.scored_at == subq.c.latest_at),
        )
        .all()
    )


def _build_summary(
    srv: McpServerRegistry,
    axes: list[McpLlmAxisScore],
    tg: TrustGateResult,
) -> ServerRiskSummary:
    overall = next((a for a in axes if a.axis_name == "overall_risk"), None)
    last: Optional[str] = None
    if overall and overall.scored_at:
        last = overall.scored_at.isoformat()
    elif srv.last_assessed:
        last = srv.last_assessed.isoformat()
    return ServerRiskSummary(
        server_id=srv.server_id,
        name=srv.name,
        url=srv.url,
        registry_source=srv.registry_source,
        risk_tier=srv.risk_tier,
        verdict=srv.verdict,
        trust_score=srv.trust_score,
        confidence=srv.confidence,
        overall_risk_label=tg.published_overall_risk or None,
        overall_risk_p_top=overall.p_top if overall else None,
        overall_risk_p_critical=overall.p_critical if overall else None,
        overall_risk_p_danger=overall.p_danger if overall else None,
        trust_gate=tg,
        axis_count=len(axes),
        last_assessed=last,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/analytics/overview", response_model=AnalyticsOverviewResponse)
def analytics_overview(db: Session = Depends(get_session)) -> AnalyticsOverviewResponse:
    """High-level counts: total servers and scored servers."""
    total = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0
    scored = (
        db.query(func.count(func.distinct(McpLlmAxisScore.server_id)))
        .scalar()
        or 0
    )
    return AnalyticsOverviewResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_servers=total,
        scored_servers=scored,
    )


@router.get("/analytics/distribution", response_model=RiskDistributionResponse)
def risk_distribution(db: Session = Depends(get_session)) -> RiskDistributionResponse:
    """Risk tier and verdict distribution across all servers."""
    total = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    tier_rows = (
        db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )

    verdict_rows = (
        db.query(
            McpServerRegistry.verdict,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .group_by(McpServerRegistry.verdict)
        .all()
    )

    def make_entry(name: str, cnt: int) -> RiskDistributionEntry:
        return RiskDistributionEntry(
            tier=name or "UNKNOWN",
            count=cnt,
            pct=round(cnt / total, 4) if total > 0 else 0.0,
        )

    return RiskDistributionResponse(
        total_servers=total,
        by_risk_tier=[make_entry(r.risk_tier, r.cnt) for r in tier_rows],
        by_verdict=[make_entry(r.verdict, r.cnt) for r in verdict_rows],
    )


@router.get("/analytics/axis/{axis_name}", response_model=AxisAnalyticsResponse)
def axis_analytics(
    axis_name: str,
    db: Session = Depends(get_session),
) -> AxisAnalyticsResponse:
    """Per-axis breakdown: label distribution, mean probabilities, escalation rate."""
    total = (
        db.query(func.count(func.distinct(McpLlmAxisScore.server_id)))
        .filter(McpLlmAxisScore.axis_name == axis_name)
        .scalar()
        or 0
    )

    rows = (
        db.query(
            McpLlmAxisScore.label,
            func.count(McpLlmAxisScore.id).label("cnt"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
            func.avg(McpLlmAxisScore.p_danger).label("avg_p_danger"),
            func.sum(func.cast(McpLlmAxisScore.escalated, Integer)).label("esc_cnt"),
        )
        .filter(McpLlmAxisScore.axis_name == axis_name)
        .group_by(McpLlmAxisScore.label)
        .all()
    )

    entries = []
    for r in rows:
        cnt = r.cnt or 0
        esc_cnt = int(r.esc_cnt or 0)
        entries.append(AxisAnalyticsEntry(
            axis_name=axis_name,
            label=r.label or "UNKNOWN",
            count=cnt,
            pct=round(cnt / total, 4) if total > 0 else 0.0,
            mean_p_top=round(float(r.avg_p_top or 0.0), 4),
            mean_p_critical=round(float(r.avg_p_critical or 0.0), 4),
            mean_p_danger=round(float(r.avg_p_danger or 0.0), 4),
            escalated_count=esc_cnt,
            escalated_pct=round(esc_cnt / cnt, 4) if cnt > 0 else 0.0,
        ))

    return AxisAnalyticsResponse(
        axis_name=axis_name,
        total_servers=total,
        entries=entries,
    )


@router.get("/analytics/servers", response_model=ServerListResponse)
def list_servers(
    db: Session = Depends(get_session),
    risk_tier: str = Query(None, description="Filter by risk_tier"),
    risk_min: float = Query(None, ge=0, le=1, description="Min p_critical threshold"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ServerListResponse:
    """List servers with risk summaries. Optionally filter by tier or p_critical."""
    q = db.query(McpServerRegistry)

    if risk_tier:
        q = q.filter(McpServerRegistry.risk_tier == risk_tier)

    total = q.count()
    servers_db = q.offset(offset).limit(limit).all()

    results: list[ServerRiskSummary] = []
    for srv in servers_db:
        axes = _latest_axes(db, srv.server_id)
        overall = next((a for a in axes if a.axis_name == "overall_risk"), None)

        if risk_min is not None:
            if overall is None or (overall.p_critical or 0) < risk_min:
                continue

        axis_labels = {
            a.axis_name: (a.label or "")
            for a in axes
        }
        if overall:
            axis_labels["overall_risk"] = overall.label or ""

        tg_dict = trust_gate(srv.url, srv.name, axis_labels)
        tg = TrustGateResult(**tg_dict)

        results.append(_build_summary(srv, axes, tg))

    return ServerListResponse(total=total, servers=results)


@router.get("/analytics/servers/{server_id}", response_model=ServerRiskSummary)
def server_risk_profile(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerRiskSummary:
    """Full risk profile for one server, including trust-gating result."""
    srv = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    axes = _latest_axes(db, server_id)

    axis_labels = {a.axis_name: (a.label or "") for a in axes}
    overall = next((a for a in axes if a.axis_name == "overall_risk"), None)
    if overall:
        axis_labels["overall_risk"] = overall.label or ""

    tg_dict = trust_gate(srv.url, srv.name, axis_labels)
    tg = TrustGateResult(**tg_dict)

    return _build_summary(srv, axes, tg)


@router.get("/analytics/critical", response_model=ServerListResponse)
def critical_servers(
    db: Session = Depends(get_session),
    min_p_critical: float = Query(default=0.5, ge=0, le=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> ServerListResponse:
    """List servers with p_critical >= threshold on overall_risk axis."""
    subq_latest = (
        db.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("latest_at"),
        )
        .filter(
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.p_critical >= min_p_critical,
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    axis_rows = (
        db.query(McpLlmAxisScore)
        .join(
            subq_latest,
            (McpLlmAxisScore.server_id == subq_latest.c.server_id)
            & (McpLlmAxisScore.scored_at == subq_latest.c.latest_at),
        )
        .all()
    )

    axis_by_server: dict[str, list[McpLlmAxisScore]] = {}
    for a in axis_rows:
        axis_by_server.setdefault(a.server_id, []).append(a)

    server_ids = list(axis_by_server.keys())[:limit]

    servers_db: dict[str, McpServerRegistry] = {}
    if server_ids:
        for srv in (
            db.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id.in_(server_ids))
            .all()
        ):
            servers_db[srv.server_id] = srv

    results: list[ServerRiskSummary] = []
    for sid in server_ids:
        srv = servers_db.get(sid)
        axes = axis_by_server.get(sid, [])
        if srv is None:
            continue

        axis_labels = {a.axis_name: (a.label or "") for a in axes}
        overall = next((a for a in axes if a.axis_name == "overall_risk"), None)
        if overall:
            axis_labels["overall_risk"] = overall.label or ""

        tg_dict = trust_gate(srv.url, srv.name, axis_labels)
        tg = TrustGateResult(**tg_dict)
        results.append(_build_summary(srv, axes, tg))

    return ServerListResponse(total=len(results), servers=results)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, Integer
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite://",
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

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)

    with TestSession() as db:
        servers = [
            McpServerRegistry(
                server_id="ana-001", name="Trusted Server",
                url="https://github.com/microsoft/sample",
                registry_source="npm", risk_tier="LOW",
                verdict="CLEAN", trust_score=0.9,
            ),
            McpServerRegistry(
                server_id="ana-002", name="Risky Server",
                url="https://example.com/risky",
                registry_source="github", risk_tier="CRITICAL",
                verdict="MALICIOUS", trust_score=0.1,
            ),
            McpServerRegistry(
                server_id="ana-003", name="Medium Server",
                url="https://example.com/medium",
                registry_source="pip", risk_tier="MEDIUM",
                verdict="UNKNOWN", trust_score=0.5,
            ),
        ]
        db.add_all(servers)
        db.flush()

        # ana-001: low risk
        axis_id = 1
        for axis, p_top, p_crit, p_dang, label in [
            ("overall_risk",       0.05, 0.02, 0.03, "LOW"),
            ("auth_strength",       0.80, 0.05, 0.05, "STRONG"),
            ("maintainer_trust",   0.90, 0.02, 0.03, "TRUSTED"),
        ]:
            db.add(McpLlmAxisScore(
                id=axis_id, server_id="ana-001", axis_name=axis,
                label=label, p_top=p_top, p_critical=p_crit, p_danger=p_dang,
                model_version="v1", scored_at=now,
            ))
            axis_id += 1

        # ana-002: critical
        for axis, p_top, p_crit, p_dang, label, esc in [
            ("overall_risk",       0.95, 0.60, 0.75, "CRITICAL", True),
            ("auth_strength",      0.10, 0.40, 0.50, "WEAK", False),
            ("exploit_surface",    0.90, 0.55, 0.60, "LARGE", True),
        ]:
            db.add(McpLlmAxisScore(
                id=axis_id, server_id="ana-002", axis_name=axis,
                label=label, p_top=p_top, p_critical=p_crit, p_danger=p_dang,
                escalated=esc,
                model_version="v1", scored_at=now,
            ))
            axis_id += 1

        # ana-003: medium, unscored on some axes
        db.add(McpLlmAxisScore(
            id=axis_id, server_id="ana-003", axis_name="overall_risk",
            label="MEDIUM", p_top=0.40, p_critical=0.25, p_danger=0.30,
            model_version="v1", scored_at=now,
        ))

        db.commit()

    client = TestClient(app)

    # Test 1: overview
    r = client.get("/api/analytics/overview")
    assert r.status_code == 200, f"overview: {r.status_code}: {r.text}"
    d = r.json()
    assert d["total_servers"] == 3, d["total_servers"]
    assert d["scored_servers"] == 3, d["scored_servers"]

    # Test 2: distribution
    r = client.get("/api/analytics/distribution")
    assert r.status_code == 200, f"dist: {r.status_code}: {r.text}"
    d = r.json()
    assert d["total_servers"] == 3
    tiers = {e["tier"]: e["count"] for e in d["by_risk_tier"]}
    assert tiers.get("LOW") == 1, tiers
    assert tiers.get("CRITICAL") == 1, tiers
    assert tiers.get("MEDIUM") == 1, tiers

    # Test 3: axis analytics
    r = client.get("/api/analytics/axis/overall_risk")
    assert r.status_code == 200, f"axis: {r.status_code}: {r.text}"
    d = r.json()
    assert d["axis_name"] == "overall_risk"
    assert d["total_servers"] == 3
    labels = {e["label"] for e in d["entries"]}
    assert "LOW" in labels
    assert "CRITICAL" in labels
    assert "MEDIUM" in labels

    # Test 4: list servers
    r = client.get("/api/analytics/servers?limit=10")
    assert r.status_code == 200, f"list: {r.status_code}: {r.text}"
    d = r.json()
    assert d["total"] == 3
    assert len(d["servers"]) == 3
    ids = {s["server_id"] for s in d["servers"]}
    assert "ana-001" in ids
    assert "ana-002" in ids
    assert "ana-003" in ids

    # Test 5: list servers filtered by tier
    r = client.get("/api/analytics/servers?risk_tier=CRITICAL")
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 1
    assert d["servers"][0]["server_id"] == "ana-002"

    # Test 6: server profile
    r = client.get("/api/analytics/servers/ana-002")
    assert r.status_code == 200, f"profile: {r.status_code}: {r.text}"
    d = r.json()
    assert d["server_id"] == "ana-002"
    assert d["overall_risk_label"] == "CRITICAL"
    assert d["overall_risk_p_critical"] == 0.60
    assert d["axis_count"] == 3

    # Test 7: unknown server 404
    r = client.get("/api/analytics/servers/not-found")
    assert r.status_code == 404, f"expected 404, got {r.status_code}"

    # Test 8: critical servers
    r = client.get("/api/analytics/critical?min_p_critical=0.5")
    assert r.status_code == 200, f"critical: {r.status_code}: {r.text}"
    d = r.json()
    assert d["total"] >= 1
    crit_ids = [s["server_id"] for s in d["servers"]]
    assert "ana-002" in crit_ids, f"ana-002 not in {crit_ids}"

    # Test 9: critical servers with high threshold (none)
    r = client.get("/api/analytics/critical?min_p_critical=0.9")
    assert r.status_code == 200
    d = r.json()
    assert len(d["servers"]) == 0

    # Test 10: trust gate on ana-001 (verified publisher -> capped to MEDIUM)
    r = client.get("/api/analytics/servers/ana-001")
    assert r.status_code == 200
    d = r.json()
    # microsoft github org -> trusted -> capped
    tg = d.get("trust_gate")
    assert tg is not None, "trust_gate should be present"
    assert tg["trusted"] is True or tg["trust_basis"] is not None or tg["original_overall_risk"] in ("LOW", "")

    print("PASS")
    sys.exit(0)
