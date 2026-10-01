# deps: fastapi, pydantic, sqlalchemy
"""Risk Tier Analysis API -- aggregate and per-server risk tier analysis over MCP servers.

GET  /api/risk-tier-analysis/summary               Overall tier distribution.
GET  /api/risk-tier-analysis/servers/{server_id}   Per-server risk tier detail.
GET  /api/risk-tier-analysis/axis/{axis_name}      Per-axis tier distribution.
GET  /api/risk-tier-analysis/by-source             Distribution broken down by registry source.
GET  /api/risk-tier-analysis/trend                 Tier counts over recent scoring runs.

Auth: public.
Data: app tier via get_session + McpServerRegistry + McpLlmAxisScore.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from app.db import get_session
from app.models import Base, McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api/risk-tier-analysis", tags=["risk_tier_analysis_api"])

# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #

class TierCount(BaseModel):
    tier: str
    count: int


class RiskTierSummary(BaseModel):
    total_servers: int
    distribution: list[TierCount]
    scored_count: int
    unscored_count: int


class ServerTierDetail(BaseModel):
    server_id: str
    name: Optional[str]
    url: Optional[str]
    registry_source: Optional[str]
    risk_tier: Optional[str]
    composite_score: Optional[float]
    overall_risk_label: Optional[str]
    axis_count: int
    last_scored_at: Optional[str]
    unscored_axes: list[str]


class ServerTierListResponse(BaseModel):
    servers: list[ServerTierDetail]
    total: int
    page: int
    page_size: int


class AxisTierDistribution(BaseModel):
    axis_name: str
    label_counts: dict[str, int]
    total_rows: int
    avg_p_top: float


class AxisDistributionResponse(BaseModel):
    axes: list[AxisTierDistribution]


class SourceTierBreakdown(BaseModel):
    source: str
    tier_counts: dict[str, int]
    total: int


class SourceDistributionResponse(BaseModel):
    sources: list[SourceTierBreakdown]


class TierTrendPoint(BaseModel):
    scored_at: str
    tier_counts: dict[str, int]
    total: int


class TierTrendResponse(BaseModel):
    points: list[TierTrendPoint]
    model_version: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

AXIS_NAMES = {
    "overall_risk", "auth_strength", "capability_breadth",
    "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
}


def _label_from_p_top(p_top: Optional[float]) -> Optional[str]:
    if p_top is None:
        return None
    if p_top >= 0.85:
        return "EXCELLENT"
    if p_top >= 0.65:
        return "GOOD"
    if p_top >= 0.45:
        return "MODERATE"
    if p_top >= 0.25:
        return "CONCERNING"
    return "CRITICAL"


def _tier_from_score(p_top: Optional[float]) -> str:
    if p_top is None:
        return "INSUFFICIENT"
    if p_top >= 0.80:
        return "TRUSTED_GENERAL"
    if p_top >= 0.60:
        return "ENTERPRISE_CONTROLLED"
    if p_top >= 0.40:
        return "CAUTION_LIMITED"
    if p_top >= 0.20:
        return "HIGH_RISK_ISOLATED"
    return "INSUFFICIENT"


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/summary", response_model=RiskTierSummary)
def get_risk_tier_summary(db: Session = Depends(get_session)) -> RiskTierSummary:
    """Overall risk-tier distribution across all scored servers."""
    total = db.execute(select(func.count()).select_from(McpServerRegistry)).scalar() or 0

    scored_rows = (
        db.execute(
            select(McpLlmAxisScore.axis_name, McpLlmAxisScore.label, func.count())
            .where(McpLlmAxisScore.axis_name == "overall_risk")
            .group_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.label)
        ).all()
    )
    scored_ids = set(
        r[0] for r in db.execute(
            select(McpLlmAxisScore.server_id)
            .where(McpLlmAxisScore.axis_name == "overall_risk")
        ).all()
    )
    scored_count = len(scored_ids)
    unscored_count = total - scored_count

    dist_map: dict[str, int] = {}
    for _, label, cnt in scored_rows:
        tier = _tier_from_score(None) if label is None else _tier_from_score(
            0.9 if label in ("EXCELLENT", "GOOD") else
            0.5 if label == "MODERATE" else
            0.3 if label == "CONCERNING" else 0.1
        )
        dist_map[tier] = dist_map.get(tier, 0) + cnt

    distribution = [TierCount(tier=t, count=c) for t, c in sorted(dist_map.items())]
    return RiskTierSummary(
        total_servers=total,
        distribution=distribution,
        scored_count=scored_count,
        unscored_count=unscored_count,
    )


@router.get("/servers/{server_id}", response_model=ServerTierDetail)
def get_server_risk_tier_detail(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerTierDetail:
    """Per-server risk tier detail: composite score, axis breakdown, unscored axes."""
    reg = db.get(McpServerRegistry, server_id)
    if not reg:
        raise HTTPException(status_code=404, detail=f"Server {server_id!r} not found")

    rows = (
        db.execute(
            select(McpLlmAxisScore)
            .where(McpLlmAxisScore.server_id == server_id)
            .order_by(McpLlmAxisScore.scored_at.desc())
            .limit(7)
        ).scalars().all()
    )

    scored_axes = {r.axis_name for r in rows}
    unscored_axes = list(AXIS_NAMES - scored_axes)

    overall_row = next((r for r in rows if r.axis_name == "overall_risk"), None)
    overall_label = overall_row.label if overall_row else None

    composite_score: Optional[float] = None
    if overall_row and overall_row.p_top is not None:
        composite_score = round(overall_row.p_top, 4)

    last_scored: Optional[datetime] = max(
        (r.scored_at for r in rows if r.scored_at), default=None
    )
    scored_at_str = (
        last_scored.isoformat() if isinstance(last_scored, datetime) else str(last_scored)
    )

    return ServerTierDetail(
        server_id=server_id,
        name=reg.name,
        url=reg.url,
        registry_source=reg.registry_source,
        risk_tier=reg.risk_tier,
        composite_score=composite_score,
        overall_risk_label=overall_label,
        axis_count=len(rows),
        last_scored_at=scored_at_str,
        unscored_axes=unscored_axes,
    )


@router.get("/servers", response_model=ServerTierListResponse)
def list_servers_by_risk_tier(
    tier: Optional[str] = Query(None, description="Filter by risk tier label"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> ServerTierListResponse:
    """Paginated list of servers optionally filtered by risk tier."""
    limit = max(1, min(page_size, 200))
    offset = (max(1, page) - 1) * limit

    conds = []
    if tier:
        sub = (
            select(McpLlmAxisScore.server_id)
            .where(McpLlmAxisScore.axis_name == "overall_risk")
        )
        # Match label to tier
        if tier.upper() in ("EXCELLENT", "GOOD"):
            sub = sub.where(McpLlmAxisScore.p_top >= 0.65)
        elif tier.upper() == "MODERATE":
            sub = sub.where(
                (McpLlmAxisScore.p_top >= 0.45) & (McpLlmAxisScore.p_top < 0.65)
            )
        elif tier.upper() == "CONCERNING":
            sub = sub.where(
                (McpLlmAxisScore.p_top >= 0.25) & (McpLlmAxisScore.p_top < 0.45)
            )
        elif tier.upper() == "CRITICAL":
            sub = sub.where(McpLlmAxisScore.p_top < 0.25)
        else:
            sub = sub.where(McpLlmAxisScore.label == tier.upper())
        conds.append(McpServerRegistry.server_id.in_(sub))

    stmt = select(McpServerRegistry)
    if conds:
        stmt = stmt.where(*conds)
    stmt = stmt.order_by(McpServerRegistry.last_seen.desc()).offset(offset).limit(limit)

    rows = db.execute(stmt).scalars().all()

    servers = []
    for reg in rows:
        overall_row = db.execute(
            select(McpLlmAxisScore)
            .where(
                McpLlmAxisScore.server_id == reg.server_id,
                McpLlmAxisScore.axis_name == "overall_risk",
            )
            .order_by(McpLlmAxisScore.scored_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        composite: Optional[float] = None
        if overall_row and overall_row.p_top is not None:
            composite = round(overall_row.p_top, 4)

        axis_count = db.execute(
            select(func.count(McpLlmAxisScore.axis_name))
            .where(McpLlmAxisScore.server_id == reg.server_id)
        ).scalar() or 0

        last_scored: Optional[datetime] = None
        if overall_row and overall_row.scored_at:
            last_scored = overall_row.scored_at

        servers.append(ServerTierDetail(
            server_id=reg.server_id,
            name=reg.name,
            url=reg.url,
            registry_source=reg.registry_source,
            risk_tier=reg.risk_tier,
            composite_score=composite,
            overall_risk_label=overall_row.label if overall_row else None,
            axis_count=axis_count,
            last_scored_at=last_scored.isoformat() if isinstance(last_scored, datetime) else str(last_scored) if last_scored else None,
            unscored_axes=[],
        ))

    total = db.execute(select(func.count()).select_from(McpServerRegistry)).scalar() or 0

    return ServerTierListResponse(
        servers=servers,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/axis/{axis_name}", response_model=AxisDistributionResponse)
def get_axis_tier_distribution(
    axis_name: str,
    db: Session = Depends(get_session),
) -> AxisDistributionResponse:
    """Per-axis label distribution across all servers."""
    if axis_name not in AXIS_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid axis_name {axis_name!r}. Valid: {', '.join(sorted(AXIS_NAMES))}"
        )

    rows = (
        db.execute(
            select(
                McpLlmAxisScore.label,
                func.count().label("cnt"),
                func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            )
            .where(McpLlmAxisScore.axis_name == axis_name)
            .group_by(McpLlmAxisScore.label)
        ).all()
    )

    label_counts: dict[str, int] = {}
    total_rows = 0
    avg_p_top = 0.0
    for label, cnt, avg_pt in rows:
        lbl = label or "UNKNOWN"
        label_counts[lbl] = int(cnt)
        total_rows += int(cnt)
        if avg_pt is not None:
            avg_p_top += float(avg_pt)

    if total_rows > 0:
        avg_p_top /= total_rows

    return AxisDistributionResponse(axes=[
        AxisTierDistribution(
            axis_name=axis_name,
            label_counts=label_counts,
            total_rows=total_rows,
            avg_p_top=round(avg_p_top, 4),
        )
    ])


@router.get("/by-source", response_model=SourceDistributionResponse)
def get_risk_tier_by_source(
    db: Session = Depends(get_session),
) -> SourceDistributionResponse:
    """Risk tier distribution broken down by registry source."""
    rows = (
        db.execute(
            select(
                McpServerRegistry.registry_source,
                McpLlmAxisScore.label,
                func.count(McpServerRegistry.server_id).label("cnt"),
            )
            .join(
                McpLlmAxisScore,
                McpServerRegistry.server_id == McpLlmAxisScore.server_id,
            )
            .where(McpLlmAxisScore.axis_name == "overall_risk")
            .group_by(McpServerRegistry.registry_source, McpLlmAxisScore.label)
        ).all()
    )

    source_map: dict[str, dict[str, int]] = {}
    for src, label, cnt in rows:
        s = src or "unknown"
        lbl = label or "UNKNOWN"
        source_map.setdefault(s, {})[lbl] = source_map[s].get(lbl, 0) + int(cnt)

    sources = []
    for src, tier_counts in sorted(source_map.items()):
        total = sum(tier_counts.values())
        sources.append(SourceTierBreakdown(
            source=src,
            tier_counts=tier_counts,
            total=total,
        ))

    return SourceDistributionResponse(sources=sources)


@router.get("/trend", response_model=TierTrendResponse)
def get_risk_tier_trend(
    days: int = Query(14, ge=1, le=90, description="Look-back window in days"),
    db: Session = Depends(get_session),
) -> TierTrendResponse:
    """Risk tier counts per scoring run day over the look-back window."""
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        db.execute(
            select(
                func.date(McpLlmAxisScore.scored_at).label("scored_date"),
                McpLlmAxisScore.label,
                func.count().label("cnt"),
            )
            .where(
                McpLlmAxisScore.axis_name == "overall_risk",
                McpLlmAxisScore.scored_at >= cutoff,
            )
            .group_by(func.date(McpLlmAxisScore.scored_at), McpLlmAxisScore.label)
            .order_by(func.date(McpLlmAxisScore.scored_at))
        ).all()
    )

    date_map: dict[str, dict[str, int]] = {}
    for date_str, label, cnt in rows:
        d = str(date_str)
        lbl = label or "UNKNOWN"
        date_map.setdefault(d, {})[lbl] = date_map[d].get(lbl, 0) + int(cnt)

    points = [
        TierTrendPoint(
            scored_at=d,
            tier_counts=tc,
            total=sum(tc.values()),
        )
        for d, tc in sorted(date_map.items())
    ]

    # Get current model version for reference
    mv = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).scalar()

    return TierTrendResponse(points=points, model_version=str(mv) if mv else "unknown")


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    def _override():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    now = datetime.now(timezone.utc)
    with TS() as db:
        # Scored server
        db.add(McpServerRegistry(server_id="s1", name="Server One",
                                url="https://github.com/example/one",
                                registry_source="github"))
        db.add(McpLlmAxisScore(id=1, server_id="s1", axis_name="overall_risk",
                               label="EXCELLENT", p_top=0.92,
                               model_version="v1", scored_at=now))
        db.add(McpLlmAxisScore(id=2, server_id="s1", axis_name="auth_strength",
                               label="STRONG", p_top=0.88,
                               model_version="v1", scored_at=now))
        # Unscored server
        db.add(McpServerRegistry(server_id="s2", name="Server Two",
                                url="https://github.com/example/two",
                                registry_source="github"))
        db.commit()

    client = TestClient(app)

    # Summary
    r = client.get("/api/risk-tier-analysis/summary")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total_servers"] == 2, d
    assert d["scored_count"] == 1, d
    assert d["unscored_count"] == 1, d

    # Server detail
    r = client.get("/api/risk-tier-analysis/servers/s1")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["server_id"] == "s1", d
    assert d["composite_score"] is not None, d
    assert d["overall_risk_label"] == "EXCELLENT", d
    assert d["axis_count"] >= 1, d

    # Server detail 404
    r = client.get("/api/risk-tier-analysis/servers/nope")
    assert r.status_code == 404, r.status_code

    # List servers
    r = client.get("/api/risk-tier-analysis/servers")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total"] == 2, d
    assert len(d["servers"]) == 2, d

    # Axis distribution
    r = client.get("/api/risk-tier-analysis/axis/overall_risk")
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["axes"]) == 1, d
    assert d["axes"][0]["axis_name"] == "overall_risk", d

    # Invalid axis
    r = client.get("/api/risk-tier-analysis/axis/bad_axis")
    assert r.status_code == 400, r.status_code

    # By source
    r = client.get("/api/risk-tier-analysis/by-source")
    assert r.status_code == 200, r.text

    # Trend
    r = client.get("/api/risk-tier-analysis/trend?days=7")
    assert r.status_code == 200, r.text

    print("PASS")
    sys.exit(0)
