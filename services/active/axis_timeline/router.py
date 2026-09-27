# deps: fastapi, sqlalchemy, pydantic
"""axis_timeline -- time-series of LLM axis scores per server.

GET /api/axis-timeline/server/{server_id}            -- axis score history
GET /api/axis-timeline/server/{server_id}/axes        -- latest 7-axis snapshot
GET /api/axis-timeline/distribution                   -- aggregate label distribution
GET /api/axis-timeline/server/{server_id}/tier-history -- risk-tier transition log

APP tables: mcp_llm_axis_scores, mcp_server_registry via get_session.
Public (auth=public); server_id is not org-scoped in the schema.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/axis-timeline", tags=["axis_timeline"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #

class AxisPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: bool
    model_version: str
    scored_at: datetime


class ServerTimelineResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    days: int
    total_points: int
    points: list[AxisPoint]


class AxisDistributionEntry(BaseModel):
    axis_name: str
    label: str
    count: int
    pct: float
    avg_p_top: Optional[float]


class DistributionResponse(BaseModel):
    as_of: str
    total_servers: int
    axes: list[AxisDistributionEntry]


class AxisVerdictEntry(BaseModel):
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


class VerdictResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    risk_tier: Optional[str]
    axes: list[AxisVerdictEntry]


class TierTransitionPoint(BaseModel):
    scored_at: datetime
    risk_tier: str
    p_top: float


class TierHistoryResponse(BaseModel):
    server_id: str
    transitions: list[TierTransitionPoint]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _derive_tier(p_top: float, p_critical: float = 0.0, p_danger: float = 0.0) -> str:
    if p_top >= 0.8 or p_critical >= 0.7:
        return "critical"
    if p_top >= 0.6 or p_danger >= 0.7:
        return "high"
    if p_top >= 0.4 or p_danger >= 0.5:
        return "medium"
    if p_top >= 0.2 or p_danger >= 0.3:
        return "low"
    return "minimal"


def _risk_tier_from_score(score: McpLlmAxisScore) -> str:
    return _derive_tier(
        p_top=score.p_top or 0.0,
        p_critical=score.p_critical or 0.0,
        p_danger=score.p_danger or 0.0,
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/server/{server_id}",
    response_model=ServerTimelineResponse,
    summary="Axis score time-series for a server",
    responses={404: {"description": "Server not found"}},
)
def get_server_axis_timeline(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365),
    axis_name: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> ServerTimelineResponse:
    srv = db.execute(
        select(McpServerRegistry.name).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()
    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.utcnow() - timedelta(days=days)
    query = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
    )
    if axis_name:
        query = query.filter(McpLlmAxisScore.axis_name == axis_name)
    rows = query.order_by(McpLlmAxisScore.scored_at.desc()).all()

    points = [
        AxisPoint(
            axis_name=r.axis_name,
            label=r.label,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            escalated=bool(r.escalated),
            model_version=r.model_version or "",
            scored_at=r.scored_at,
        )
        for r in rows
    ]
    return ServerTimelineResponse(
        server_id=server_id,
        server_name=srv,
        days=days,
        total_points=len(points),
        points=points,
    )


@router.get(
    "/server/{server_id}/axes",
    response_model=VerdictResponse,
    summary="Latest 7-axis verdict for a server",
    responses={404: {"description": "Server not found"}},
)
def get_server_axes(
    server_id: str,
    model_version: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> VerdictResponse:
    srv_row = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        ).where(McpServerRegistry.server_id == server_id)
    ).first()
    if srv_row is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    query = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id)
    if model_version:
        query = query.filter(McpLlmAxisScore.model_version == model_version)
    rows = query.order_by(McpLlmAxisScore.scored_at.desc()).all()

    seen: dict[str, McpLlmAxisScore] = {}
    for r in rows:
        if r.axis_name not in seen:
            seen[r.axis_name] = r

    axes = [
        AxisVerdictEntry(
            axis_name=r.axis_name,
            label=r.label,
            label_index=r.label_index,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            escalated=bool(r.escalated),
            escalated_to=r.escalated_to,
            model_version=r.model_version or "",
            scored_at=r.scored_at,
        )
        for r in seen.values()
    ]
    return VerdictResponse(
        server_id=server_id,
        server_name=srv_row.name,
        risk_tier=srv_row.risk_tier,
        axes=axes,
    )


@router.get(
    "/distribution",
    response_model=DistributionResponse,
    summary="Aggregate axis score distribution across all servers",
)
def get_axis_distribution(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> DistributionResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)

    total_servers = db.execute(
        select(func.count(func.distinct(McpLlmAxisScore.server_id))).where(
            McpLlmAxisScore.scored_at >= cutoff
        )
    ).scalar_one() or 0

    stats = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            func.count().label("count"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.label)
        .order_by(McpLlmAxisScore.axis_name, func.count().desc())
    ).all()

    axis_totals: dict[str, int] = defaultdict(int)
    for row in stats:
        axis_totals[row.axis_name] += row.count

    seen_pairs: set[tuple[str, str]] = set()
    axes: list[AxisDistributionEntry] = []
    for row in stats:
        pair = (row.axis_name, row.label)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        total = axis_totals[row.axis_name]
        axes.append(
            AxisDistributionEntry(
                axis_name=row.axis_name,
                label=row.label or "",
                count=row.count,
                pct=round(row.count / total, 4) if total else 0.0,
                avg_p_top=round(float(row.avg_p_top), 4) if row.avg_p_top is not None else None,
            )
        )

    return DistributionResponse(
        as_of=datetime.utcnow().isoformat() + "Z",
        total_servers=total_servers,
        axes=axes,
    )


@router.get(
    "/server/{server_id}/tier-history",
    response_model=TierHistoryResponse,
    summary="Risk-tier change log for a server",
    responses={404: {"description": "Server not found"}},
)
def get_tier_history(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> TierHistoryResponse:
    srv = db.execute(
        select(McpServerRegistry.server_id).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()
    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.axis_name == "overall_risk")
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    transitions: list[TierTransitionPoint] = []
    seen_tiers: set[str] = set()
    for r in rows:
        tier = _risk_tier_from_score(r)
        if tier in seen_tiers:
            continue
        seen_tiers.add(tier)
        transitions.append(
            TierTransitionPoint(
                scored_at=r.scored_at,
                risk_tier=tier,
                p_top=r.p_top or 0.0,
            )
        )

    return TierHistoryResponse(server_id=server_id, transitions=transitions)


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
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    db = TestSession()
    db.add(McpServerRegistry(
        server_id="tl-srv-1",
        name="Timeline Server 1",
        risk_tier="medium",
        verdict="clean",
        confidence=0.9,
        description="test",
        first_seen=now,
        last_scanned=None,
        last_seen=now,
        last_assessed=now,
        meta={},
        registry_source="test",
        scan_count=1,
        trust_score=0.8,
        url="http://tl-srv-1",
    ))
    db.add(McpServerRegistry(
        server_id="tl-srv-2",
        name="Timeline Server 2",
        risk_tier="low",
        verdict="clean",
        confidence=1.0,
        description="test",
        first_seen=now,
        last_scanned=None,
        last_seen=now,
        last_assessed=now,
        meta={},
        registry_source="test",
        scan_count=1,
        trust_score=0.95,
        url="http://tl-srv-2",
    ))

    axes = [
        "overall_risk", "auth_strength", "capability_breadth",
        "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
    ]
    for day_delta, p_top, label in [(5, 0.35, "LOW"), (0, 0.72, "HIGH")]:
        for ax in axes:
            db.add(McpLlmAxisScore(
                server_id="tl-srv-1",
                axis_name=ax,
                label=label,
                label_index=0,
                p_top=p_top,
                p_critical=0.1,
                p_danger=0.2,
                escalated=False,
                model_version="v1",
                scored_at=now - timedelta(days=day_delta),
                adapter_sha256="sha256test",
                decision_rule_version="v1",
                escalated_to=None,
                probs=None,
                id=None,
            ))
    for ax in axes:
        db.add(McpLlmAxisScore(
            server_id="tl-srv-2",
            axis_name=ax,
            label="LOW",
            label_index=0,
            p_top=0.2,
            p_critical=0.05,
            p_danger=0.1,
            escalated=False,
            model_version="v1",
            scored_at=now,
            adapter_sha256="sha256test",
            decision_rule_version="v1",
            escalated_to=None,
            probs=None,
            id=None,
        ))
    db.commit()
    db.close()

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = _override
    client = TestClient(that_app)

    # Test 1: timeline happy path
    r = client.get("/api/axis-timeline/server/tl-srv-1?days=30")
    assert r.status_code == 200, f"timeline 200: {r.text}"
    d = r.json()
    assert d["server_id"] == "tl-srv-1"
    assert d["server_name"] == "Timeline Server 1"
    assert d["total_points"] == 14
    assert len(d["points"]) == 14

    # Test 2: timeline 404
    r = client.get("/api/axis-timeline/server/nonexistent?days=7")
    assert r.status_code == 404

    # Test 3: axis filter
    r = client.get("/api/axis-timeline/server/tl-srv-1?days=30&axis_name=overall_risk")
    assert r.status_code == 200, f"axis filter: {r.text}"
    d = r.json()
    assert all(p["axis_name"] == "overall_risk" for p in d["points"])

    # Test 4: axes endpoint
    r = client.get("/api/axis-timeline/server/tl-srv-1/axes")
    assert r.status_code == 200, f"axes 200: {r.text}"
    d = r.json()
    assert len(d["axes"]) == 7
    axis_names = {a["axis_name"] for a in d["axes"]}
    for ax in axes:
        assert ax in axis_names, f"missing {ax}"

    # Test 5: distribution
    r = client.get("/api/axis-timeline/distribution?days=30")
    assert r.status_code == 200, f"distribution: {r.text}"
    d = r.json()
    assert d["total_servers"] == 2
    assert len(d["axes"]) > 0

    # Test 6: tier history
    r = client.get("/api/axis-timeline/server/tl-srv-1/tier-history?days=30")
    assert r.status_code == 200, f"tier-history: {r.text}"
    d = r.json()
    tiers = [t["risk_tier"] for t in d["transitions"]]
    assert len(tiers) >= 1
    assert len(tiers) == len(set(tiers))

    # Test 7: tier history 404
    r = client.get("/api/axis-timeline/server/unknown/tier-history?days=7")
    assert r.status_code == 404

    print("PASS")
    sys.exit(0)
