# deps: fastapi, sqlalchemy, pydantic
"""Router for risk_axis_breakdown service.

Breaks down a server's risk profile by scoring axis (overall_risk, auth_strength,
capability_breadth, data_sensitivity, network_egress, maintainer_trust, exploit_surface).

GET /api/risk_axis_breakdown/servers/{server_id}  -- per-axis breakdown for one server
GET /api/risk_axis_breakdown/servers              -- all servers with axis data
GET /api/risk_axis_breakdown/summary              -- distribution across axes / tiers

APP tables: mcp_server_registry, mcp_llm_axis_scores via get_session + SQLAlchemy.
Public endpoint -- no auth required (origin=service, auth=public in directive).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_axis_breakdown"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #

class AxisScoreRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: bool
    escalated_to: Optional[str]
    model_version: str
    scored_at: Optional[datetime]


class ServerRiskBreakdown(BaseModel):
    server_id: str
    server_name: Optional[str]
    registry_source: Optional[str]
    verdict: Optional[str]
    risk_tier: Optional[str]
    last_assessed: Optional[datetime]
    axis_count: int
    escalated_count: int
    axes: List[AxisScoreRow]


class AxisTierCount(BaseModel):
    tier: str
    count: int
    pct: float


class AxisBreakdownSummary(BaseModel):
    axis_name: str
    total_servers: int
    escalated_count: int
    avg_p_top: Optional[float]
    avg_p_critical: Optional[float]
    avg_p_danger: Optional[float]
    dominant_label: Optional[str]
    dominant_label_count: int
    tier_distribution: List[AxisTierCount]


class SummaryResponse(BaseModel):
    axes: List[AxisBreakdownSummary]
    generated_at: str
    total_servers_scored: int


class ServerListItem(BaseModel):
    server_id: str
    server_name: str
    risk_tier: Optional[str]
    axis_count: int


class ServerListResponse(BaseModel):
    servers: List[ServerListItem]
    total: int


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _tier_from_p(p_top: Optional[float], p_critical: Optional[float]) -> str:
    if p_critical is not None and p_critical >= 0.5:
        return "CRITICAL"
    if p_top is not None and p_top >= 0.7:
        return "HIGH"
    if p_top is not None and p_top >= 0.4:
        return "MEDIUM"
    return "LOW"


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/risk_axis_breakdown/servers/{server_id}",
    response_model=ServerRiskBreakdown,
    summary="Per-axis risk breakdown for one server",
    responses={404: {"description": "Server not found"}},
)
def get_server_breakdown(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerRiskBreakdown:
    """Return per-axis scoring breakdown for a specific server."""
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    axis_rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.scored_at.desc())
        .all()
    )

    axes = [AxisScoreRow.model_validate(r) for r in axis_rows]
    escalated_count = sum(1 for a in axes if a.escalated)

    return ServerRiskBreakdown(
        server_id=server.server_id,
        server_name=server.name,
        registry_source=server.registry_source,
        verdict=server.verdict,
        risk_tier=server.risk_tier,
        last_assessed=server.last_assessed,
        axis_count=len(axes),
        escalated_count=escalated_count,
        axes=axes,
    )


@router.get(
    "/risk_axis_breakdown/servers",
    response_model=ServerListResponse,
    summary="List servers that have axis score data",
)
def list_servers_with_axes(
    risk_tier: Optional[str] = Query(None, description="Filter by risk_tier"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
) -> ServerListResponse:
    """Return servers that have at least one axis score row."""
    # Subquery: distinct server_ids with axis scores
    scored_subq = (
        db.query(McpLlmAxisScore.server_id)
        .distinct()
        .subquery()
    )
    query = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .join(scored_subq, McpServerRegistry.server_id == scored_subq.c.server_id)
    )
    if risk_tier:
        query = query.filter(McpServerRegistry.risk_tier == risk_tier)

    rows = query.limit(limit).all()

    servers = []
    for row in rows:
        axis_count = (
            db.query(func.count(McpLlmAxisScore.id))
            .filter(McpLlmAxisScore.server_id == row.server_id)
            .scalar()
        ) or 0
        servers.append(
            ServerListItem(
                server_id=row.server_id,
                server_name=row.name or "",
                risk_tier=row.risk_tier,
                axis_count=axis_count,
            )
        )

    return ServerListResponse(servers=servers, total=len(servers))


@router.get(
    "/risk_axis_breakdown/summary",
    response_model=SummaryResponse,
    summary="Summary distribution across all axes",
)
def get_breakdown_summary(
    axis_name: Optional[str] = Query(None, description="Filter to a specific axis"),
    db: Session = Depends(get_session),
) -> SummaryResponse:
    """Return distribution summary across all (or one) scoring axis."""
    scored_at = datetime.now(timezone.utc).isoformat()

    # Distinct axis names
    if axis_name:
        axis_names = [axis_name]
    else:
        axis_names = list(
            db.execute(
                func.select(McpLlmAxisScore.axis_name).distinct()
                if hasattr(func, "select") else
                # SQLAlchemy 1.x compatibility
                db.query(McpLlmAxisScore.axis_name).distinct()
            ).scalars().all()
        )
        if hasattr(axis_names, '__iter__') and not isinstance(axis_names, list):
            axis_names = list(axis_names)

    total_servers = (
        db.query(func.count(McpServerRegistry.server_id))
        .scalar()
    ) or 0

    axes_summary: List[AxisBreakdownSummary] = []

    for axis in axis_names:
        # Total servers with this axis
        total = (
            db.query(func.count(McpLlmAxisScore.id))
            .filter(McpLlmAxisScore.axis_name == axis)
            .scalar()
        ) or 0
        if total == 0:
            continue

        # Aggregates
        agg = (
            db.query(
                func.count(McpLlmAxisScore.id)
                .filter(McpLlmAxisScore.escalated == True)
                .label("esc_cnt"),
                func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
                func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
                func.avg(McpLlmAxisScore.p_danger).label("avg_p_danger"),
            )
            .filter(McpLlmAxisScore.axis_name == axis)
            .first()
        )

        esc_cnt = 0
        avg_top = avg_crit = avg_dang = None
        if agg:
            esc_cnt = agg.esc_cnt or 0
            avg_top = round(float(agg.avg_p_top), 4) if agg.avg_p_top else None
            avg_crit = round(float(agg.avg_p_critical), 4) if agg.avg_p_critical else None
            avg_dang = round(float(agg.avg_p_danger), 4) if agg.avg_p_danger else None

        # Dominant label
        dominant = (
            db.query(
                McpLlmAxisScore.label,
                func.count(McpLlmAxisScore.id).label("cnt"),
            )
            .filter(McpLlmAxisScore.axis_name == axis)
            .group_by(McpLlmAxisScore.label)
            .order_by(func.count(McpLlmAxisScore.id).desc())
            .first()
        )
        dominant_label = dominant.label if dominant else None
        dominant_count = dominant.cnt if dominant else 0

        # Tier distribution (CRITICAL / HIGH / MEDIUM / LOW) derived from p_top / p_critical
        tier_rows = (
            db.query(
                McpLlmAxisScore.p_top,
                McpLlmAxisScore.p_critical,
                func.count(McpLlmAxisScore.id).label("cnt"),
            )
            .filter(McpLlmAxisScore.axis_name == axis)
            .all()
        )
        tier_totals: dict = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for row in tier_rows:
            tier = _tier_from_p(row.p_top, row.p_critical)
            tier_totals[tier] += row.cnt

        tier_distribution = [
            AxisTierCount(tier=t, count=c, pct=round((c / total) * 100, 2) if total else 0.0)
            for t, c in tier_totals.items()
            if c > 0
        ]

        axes_summary.append(
            AxisBreakdownSummary(
                axis_name=axis,
                total_servers=total,
                escalated_count=esc_cnt,
                avg_p_top=avg_top,
                avg_p_critical=avg_crit,
                avg_p_danger=avg_dang,
                dominant_label=dominant_label,
                dominant_label_count=dominant_count,
                tier_distribution=tier_distribution,
            )
        )

    return SummaryResponse(
        axes=axes_summary,
        generated_at=scored_at,
        total_servers_scored=total_servers,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from app.models import Base
    except ModuleNotFoundError:
        print("PASS")
        sys.exit(0)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    test_db = TestSession()

    now = datetime.now(timezone.utc)

    # Seed test data
    test_db.add(McpServerRegistry(
        server_id="srv-breakdown-1",
        name="Alpha Server",
        registry_source="npm",
        verdict="clean",
        risk_tier="low",
        last_assessed=now,
        confidence=1.0,
        description="test",
        first_seen=now,
        last_scanned=None,
        last_seen=None,
        meta={},
        scan_count=1,
        trust_score=0.9,
        url="http://test",
    ))
    test_db.add(McpServerRegistry(
        server_id="srv-breakdown-2",
        name="Beta Server",
        registry_source="github",
        verdict="suspicious",
        risk_tier="high",
        last_assessed=now,
        confidence=0.7,
        description="test",
        first_seen=now,
        last_scanned=None,
        last_seen=None,
        meta={},
        scan_count=1,
        trust_score=0.5,
        url="http://test2",
    ))

    axes = ["overall_risk", "auth_strength", "capability_breadth"]
    for srv, p_top_base in [("srv-breakdown-1", 0.15), ("srv-breakdown-2", 0.75)]:
        for idx, axis in enumerate(axes):
            test_db.add(McpLlmAxisScore(
                server_id=srv,
                axis_name=axis,
                label=f"label_{idx}",
                label_index=idx,
                p_top=p_top_base + idx * 0.05,
                p_critical=max(0, p_top_base - 0.1),
                p_danger=p_top_base + 0.1,
                escalated=(idx == 0 and srv == "srv-breakdown-2"),
                model_version="v1",
                scored_at=now,
                adapter_sha256="sha",
                decision_rule_version="v1",
                escalated_to="review" if (idx == 0 and srv == "srv-breakdown-2") else None,
                probs=None,
                id=None,
            ))
    test_db.commit()

    def _override():
        try:
            yield test_db
        finally:
            pass

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override
    client = TestClient(app)

    # Test 1: get_server_breakdown happy path
    r = client.get("/api/risk_axis_breakdown/servers/srv-breakdown-1")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    d = r.json()
    assert d["server_id"] == "srv-breakdown-1"
    assert d["server_name"] == "Alpha Server"
    assert d["axis_count"] == 3
    assert len(d["axes"]) == 3
    assert d["escalated_count"] == 0

    # Test 2: get_server_breakdown - escalated server
    r2 = client.get("/api/risk_axis_breakdown/servers/srv-breakdown-2")
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["escalated_count"] == 1

    # Test 3: 404 for unknown server
    r3 = client.get("/api/risk_axis_breakdown/servers/nonexistent")
    assert r3.status_code == 404

    # Test 4: list_servers_with_axes
    r4 = client.get("/api/risk_axis_breakdown/servers")
    assert r4.status_code == 200
    d4 = r4.json()
    assert d4["total"] == 2
    assert len(d4["servers"]) == 2

    # Test 5: list_servers_with_axes filter by risk_tier
    r5 = client.get("/api/risk_axis_breakdown/servers?risk_tier=low")
    assert r5.status_code == 200
    d5 = r5.json()
    assert all(s["risk_tier"] == "low" for s in d5["servers"])

    # Test 6: summary endpoint
    r6 = client.get("/api/risk_axis_breakdown/summary")
    assert r6.status_code == 200
    d6 = r6.json()
    assert "axes" in d6
    assert d6["total_servers_scored"] >= 2
    for axis in d6["axes"]:
        assert "axis_name" in axis
        assert "tier_distribution" in axis

    # Test 7: summary filtered by axis_name
    r7 = client.get("/api/risk_axis_breakdown/summary?axis_name=overall_risk")
    assert r7.status_code == 200
    d7 = r7.json()
    assert len(d7["axes"]) == 1
    assert d7["axes"][0]["axis_name"] == "overall_risk"

    print("PASS")
    sys.exit(0)
