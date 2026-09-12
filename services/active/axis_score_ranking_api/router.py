# deps: fastapi, pydantic, sqlalchemy
"""axis_score_ranking_api – ranks MCP servers by axis score across multiple axes.

GET /api/axis-score-ranking              ranked list of servers across one or all axes
GET /api/axis-score-ranking/summary      aggregate distribution counts per axis
GET /api/axis-score-ranking/axis/{axis}  top-N servers for a specific axis

Auth: public.
Data: app Postgres via get_session + SQLAlchemy ORM on
  mcp_server_registry, mcp_llm_axis_scores.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["axis_score_ranking_api"])

# All 7 axis names from the schema
ALL_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]

# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class AxisScoreEntry(BaseModel):
    """One axis's score for a server."""
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    scored_at: Optional[datetime] = None


class RankedServer(BaseModel):
    """One server in the ranked list."""
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    composite_score: float = Field(ge=0.0, le=100.0, description="Weighted composite score")
    primary_axis: str = Field(description="Axis used for primary sort")
    axes: list[AxisScoreEntry] = Field(default_factory=list)
    last_assessed: Optional[str] = None


class AxisRankingResponse(BaseModel):
    """Ranked list of servers by axis score."""
    axis_name: str
    generated_at: str
    total_servers: int
    ranked_servers: list[RankedServer]


class AxisSummaryBucket(BaseModel):
    risk_tier: str
    count: int


class AxisSummary(BaseModel):
    axis_name: str
    total_servers: int
    scored_servers: int
    unscored_servers: int
    buckets: list[AxisSummaryBucket] = Field(default_factory=list)
    avg_p_top: Optional[float] = None


class AxisRankingSummaryResponse(BaseModel):
    """Aggregate summary across all axes or a specific axis."""
    axis_name: Optional[str] = None
    generated_at: str
    axes: list[AxisSummary] = Field(default_factory=list)


class ServerAxisDetail(BaseModel):
    """All-axis scores for one server."""
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    axes: list[AxisScoreEntry] = Field(default_factory=list)
    composite_score: float = Field(ge=0.0, le=100.0)
    last_assessed: Optional[str] = None


class ServerAxisDetailResponse(BaseModel):
    """Full multi-axis profile for a single server."""
    generated_at: str
    server: ServerAxisDetail


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _latest_for_server(
    db: Session,
    server_id: str,
) -> list[McpLlmAxisScore]:
    """Return the most-recently-scored row per axis for a server."""
    subq = (
        db.query(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("latest_at"),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )
    rows = (
        db.query(McpLlmAxisScore)
        .join(
            subq,
            (McpLlmAxisScore.server_id == server_id)
            & (McpLlmAxisScore.axis_name == subq.c.axis_name)
            & (McpLlmAxisScore.scored_at == subq.c.latest_at),
        )
        .all()
    )
    return rows


def _composite_score(rows: list[McpLlmAxisScore]) -> float:
    """Compute a 0-100 composite score from axis rows using overall_risk p_top."""
    overall = next((r for r in rows if r.axis_name == "overall_risk"), None)
    if not overall or overall.p_top is None:
        return 0.0
    # p_top is the probability of the top label (higher = more confident)
    # Convert to a 0-100 scale where 1.0 p_top → 100
    return round(float(overall.p_top) * 100, 4)


def _build_ranked_server(
    server_id: str,
    axes: list[McpLlmAxisScore],
    srv: Optional[McpServerRegistry],
    primary_axis: str,
) -> RankedServer:
    entries = [
        AxisScoreEntry(
            axis_name=a.axis_name,
            label=a.label,
            label_index=a.label_index,
            p_top=a.p_top,
            p_critical=a.p_critical,
            p_danger=a.p_danger,
            scored_at=a.scored_at,
        )
        for a in axes
    ]

    composite = _composite_score(axes)

    last_assessed: Optional[str] = None
    overall = next((a for a in axes if a.axis_name == "overall_risk"), None)
    if overall and overall.scored_at:
        last_assessed = overall.scored_at.isoformat()
    elif srv and srv.last_assessed:
        last_assessed = srv.last_assessed.isoformat()

    return RankedServer(
        server_id=server_id,
        name=srv.name if srv else None,
        registry_source=srv.registry_source if srv else None,
        risk_tier=srv.risk_tier if srv else None,
        verdict=srv.verdict if srv else None,
        composite_score=composite,
        primary_axis=primary_axis,
        axes=entries,
        last_assessed=last_assessed,
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/axis-score-ranking", response_model=AxisRankingResponse)
def get_axis_ranking(
    axis_name: str = Query(
        default="overall_risk",
        description="Axis to rank by. Use 'overall_risk' for composite ranking.",
    ),
    limit: int = Query(default=100, ge=1, le=500, description="Max servers to return"),
    direction: str = Query(
        default="desc",
        description="'desc': highest p_top first (riskiest); 'asc': lowest first",
    ),
    db: Session = Depends(get_session),
) -> AxisRankingResponse:
    """
    Return servers ranked by their axis score for a given axis.
    Results are sorted by p_top (probability of the top label).

    If the axis is not in the 7 known axes, returns 422.
    """
    if axis_name not in ALL_AXES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown axis '{axis_name}'. Valid axes: {ALL_AXES}",
        )

    # Subquery: latest scored_at per server for this axis
    subq_latest = (
        db.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("latest_at"),
        )
        .filter(McpLlmAxisScore.axis_name == axis_name)
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # Latest row per server for this axis
    axis_rows = (
        db.query(McpLlmAxisScore)
        .join(
            subq_latest,
            (McpLlmAxisScore.server_id == subq_latest.c.server_id)
            & (McpLlmAxisScore.scored_at == subq_latest.c.latest_at)
            & (McpLlmAxisScore.axis_name == axis_name),
        )
        .all()
    )

    total = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    # Sort
    asc = direction == "asc"
    axis_rows.sort(key=lambda r: r.p_top or 0.0, reverse=not asc)

    # Build profiles using all-axis data for composite score
    profiles: list[RankedServer] = []
    for row in axis_rows[:limit]:
        all_axes = _latest_for_server(db, row.server_id)
        srv = (
            db.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id == row.server_id)
            .first()
        )
        profiles.append(_build_ranked_server(row.server_id, all_axes, srv, axis_name))

    return AxisRankingResponse(
        axis_name=axis_name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_servers=total,
        ranked_servers=profiles,
    )


@router.get("/axis-score-ranking/summary", response_model=AxisRankingSummaryResponse)
def get_axis_ranking_summary(
    axis_name: Optional[str] = Query(
        default=None,
        description="Specific axis. Omit for all axes.",
    ),
    db: Session = Depends(get_session),
) -> AxisRankingSummaryResponse:
    """
    Return aggregate distribution (count per risk_tier) for each axis.
    Useful for dashboards and comparison views.
    """
    axes_to_process = [axis_name] if axis_name else ALL_AXES

    summaries: list[AxisSummary] = []
    for axis in axes_to_process:
        if axis not in ALL_AXES:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown axis '{axis}'. Valid axes: {ALL_AXES}",
            )

        # Latest score per server for this axis
        subq_latest = (
            db.query(
                McpLlmAxisScore.server_id,
                func.max(McpLlmAxisScore.scored_at).label("latest_at"),
            )
            .filter(McpLlmAxisScore.axis_name == axis)
            .group_by(McpLlmAxisScore.server_id)
            .subquery()
        )

        latest_rows = (
            db.query(McpLlmAxisScore)
            .join(
                subq_latest,
                (McpLlmAxisScore.server_id == subq_latest.c.server_id)
                & (McpLlmAxisScore.scored_at == subq_latest.c.latest_at)
                & (McpLlmAxisScore.axis_name == axis),
            )
            .all()
        )

        scored_sids = {r.server_id for r in latest_rows}
        total = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

        # Bucket by risk_tier from registry
        tier_counts: dict[str, int] = {}
        if scored_sids:
            rows = (
                db.query(McpServerRegistry.risk_tier, func.count())
                .filter(McpServerRegistry.server_id.in_(scored_sids))
                .group_by(McpServerRegistry.risk_tier)
                .all()
            )
            tier_counts = {r[0] or "unknown": r[1] for r in rows}

        avg_p = None
        p_vals = [r.p_top for r in latest_rows if r.p_top is not None]
        if p_vals:
            avg_p = round(sum(p_vals) / len(p_vals), 4)

        buckets = [
            AxisSummaryBucket(risk_tier=k, count=v)
            for k, v in sorted(tier_counts.items())
        ]

        summaries.append(
            AxisSummary(
                axis_name=axis,
                total_servers=total,
                scored_servers=len(scored_sids),
                unscored_servers=total - len(scored_sids),
                buckets=buckets,
                avg_p_top=avg_p,
            )
        )

    return AxisRankingSummaryResponse(
        axis_name=axis_name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        axes=summaries,
    )


@router.get(
    "/axis-score-ranking/axis/{axis_name}",
    response_model=AxisRankingResponse,
    summary="Top-N servers for a specific axis",
)
def get_axis_top(
    axis_name: str,
    limit: int = Query(default=50, ge=1, le=500),
    direction: str = Query(
        default="desc",
        description="'desc': highest p_top first; 'asc': lowest first",
    ),
    db: Session = Depends(get_session),
) -> AxisRankingResponse:
    """
    Alias for /axis-score-ranking that also validates axis_name is in the URL path.
    Returns top-N servers for the named axis, sorted by p_top.
    """
    return get_axis_ranking(
        axis_name=axis_name,
        limit=limit,
        direction=direction,
        db=db,
    )


@router.get(
    "/axis-score-ranking/server/{server_id}",
    response_model=ServerAxisDetailResponse,
    summary="All-axis profile for a single server",
)
def get_server_axis_profile(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerAxisDetailResponse:
    """
    Return the complete multi-axis score profile for a single server.
    """
    srv = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    axes = _latest_for_server(db, server_id)

    if not srv and not axes:
        raise HTTPException(
            status_code=404,
            detail=f"Server '{server_id}' not found",
        )

    entries = [
        AxisScoreEntry(
            axis_name=a.axis_name,
            label=a.label,
            label_index=a.label_index,
            p_top=a.p_top,
            p_critical=a.p_critical,
            p_danger=a.p_danger,
            scored_at=a.scored_at,
        )
        for a in axes
    ]

    composite = _composite_score(axes)

    last_assessed: Optional[str] = None
    overall = next((a for a in axes if a.axis_name == "overall_risk"), None)
    if overall and overall.scored_at:
        last_assessed = overall.scored_at.isoformat()
    elif srv and srv.last_assessed:
        last_assessed = srv.last_assessed.isoformat()

    return ServerAxisDetailResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        server=ServerAxisDetail(
            server_id=server_id,
            name=srv.name if srv else None,
            registry_source=srv.registry_source if srv else None,
            risk_tier=srv.risk_tier if srv else None,
            verdict=srv.verdict if srv else None,
            axes=entries,
            composite_score=composite,
            last_assessed=last_assessed,
        ),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys as _sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)

    # Seed test data
    with TestSessionLocal() as db:
        servers = [
            McpServerRegistry(
                server_id="srv-1",
                name="Safe Server",
                registry_source="npm",
                risk_tier="LOW",
                verdict="safe",
                last_assessed=now,
            ),
            McpServerRegistry(
                server_id="srv-2",
                name="Risky Server",
                registry_source="github",
                risk_tier="CRITICAL",
                verdict="dangerous",
                last_assessed=now,
            ),
            McpServerRegistry(
                server_id="srv-3",
                name="Unscored Server",
                registry_source="npm",
                risk_tier=None,
                verdict=None,
                last_assessed=now,
            ),
        ]
        db.add_all(servers)
        db.flush()

        # srv-1: low risk
        for idx, (axis, p_top_val, label) in enumerate([
            ("overall_risk",       0.05, "LOW"),
            ("auth_strength",      0.80, "LOW"),
            ("capability_breadth", 0.60, "MEDIUM"),
            ("data_sensitivity",   0.10, "LOW"),
            ("network_egress",     0.20, "LOW"),
            ("maintainer_trust",   0.90, "LOW"),
            ("exploit_surface",    0.05, "LOW"),
        ]):
            db.add(McpLlmAxisScore(
                id=100 + idx,
                server_id="srv-1", axis_name=axis,
                label=label, label_index=0 if label == "LOW" else 2,
                p_top=p_top_val, p_critical=0.02, p_danger=0.03,
                model_version="v1", scored_at=now,
            ))

        # srv-2: critical risk
        for idx, (axis, p_top_val, label) in enumerate([
            ("overall_risk",       0.95, "CRITICAL"),
            ("auth_strength",      0.10, "CRITICAL"),
            ("capability_breadth", 0.90, "CRITICAL"),
            ("data_sensitivity",   0.85, "HIGH"),
            ("network_egress",     0.75, "HIGH"),
            ("maintainer_trust",   0.05, "CRITICAL"),
            ("exploit_surface",    0.80, "CRITICAL"),
        ]):
            db.add(McpLlmAxisScore(
                id=200 + idx,
                server_id="srv-2", axis_name=axis,
                label=label, label_index=3 if label == "CRITICAL" else 2,
                p_top=p_top_val, p_critical=0.55, p_danger=0.70,
                model_version="v1", scored_at=now,
            ))

        db.commit()

    client = TestClient(app)

    # --- Test ranked list: highest p_top first ---
    resp1 = client.get("/api/axis-score-ranking", params={"axis_name": "overall_risk", "limit": 10})
    assert resp1.status_code == 200, f"ranked: {resp1.status_code}: {resp1.text}"
    d1 = resp1.json()
    assert d1["axis_name"] == "overall_risk"
    assert d1["total_servers"] == 3
    # srv-2 (p_top=0.95) should be first, srv-1 (p_top=0.05) second
    ranked_ids = [r["server_id"] for r in d1["ranked_servers"]]
    assert ranked_ids[0] == "srv-2", f"srv-2 first, got {ranked_ids}"
    assert ranked_ids[1] == "srv-1", f"srv-1 second, got {ranked_ids}"
    # composite = p_top * 100
    assert d1["ranked_servers"][0]["composite_score"] == 95.0
    assert d1["ranked_servers"][1]["composite_score"] == 5.0

    # --- Test direction=asc (lowest first) ---
    resp1a = client.get("/api/axis-score-ranking", params={"axis_name": "overall_risk", "direction": "asc"})
    assert resp1a.status_code == 200
    d1a = resp1a.json()
    asc_ids = [r["server_id"] for r in d1a["ranked_servers"]]
    assert asc_ids[0] == "srv-1", f"asc: srv-1 first, got {asc_ids}"

    # --- Test path alias endpoint ---
    resp2 = client.get("/api/axis-score-ranking/axis/overall_risk", params={"limit": 5})
    assert resp2.status_code == 200, f"axis alias: {resp2.status_code}: {resp2.text}"
    assert resp2.json()["axis_name"] == "overall_risk"

    # --- Test summary ---
    resp3 = client.get("/api/axis-score-ranking/summary")
    assert resp3.status_code == 200, f"summary: {resp3.status_code}: {resp3.text}"
    d3 = resp3.json()
    assert len(d3["axes"]) == 7  # all 7 axes
    overall_summary = next(a for a in d3["axes"] if a["axis_name"] == "overall_risk")
    assert overall_summary["scored_servers"] == 2
    assert overall_summary["unscored_servers"] == 1

    # --- Test summary for specific axis ---
    resp3a = client.get("/api/axis-score-ranking/summary", params={"axis_name": "auth_strength"})
    assert resp3a.status_code == 200
    d3a = resp3a.json()
    assert len(d3a["axes"]) == 1
    assert d3a["axes"][0]["axis_name"] == "auth_strength"

    # --- Test server profile ---
    resp4 = client.get("/api/axis-score-ranking/server/srv-2")
    assert resp4.status_code == 200, f"profile: {resp4.status_code}: {resp4.text}"
    d4 = resp4.json()
    assert d4["server"]["server_id"] == "srv-2"
    assert d4["server"]["risk_tier"] == "CRITICAL"
    assert d4["server"]["composite_score"] == 95.0
    assert len(d4["server"]["axes"]) == 7

    # --- Test unscored server profile (no axis rows) ---
    resp5 = client.get("/api/axis-score-ranking/server/srv-3")
    assert resp5.status_code == 200, f"srv-3: {resp5.status_code}: {resp5.text}"
    d5 = resp5.json()
    assert d5["server"]["server_id"] == "srv-3"
    assert d5["server"]["composite_score"] == 0.0
    assert d5["server"]["axes"] == []

    # --- Test 404 for unknown server ---
    resp6 = client.get("/api/axis-score-ranking/server/not-found")
    assert resp6.status_code == 404, f"expected 404, got {resp6.status_code}"

    # --- Test 422 for unknown axis ---
    resp7 = client.get("/api/axis-score-ranking", params={"axis_name": "not_a_real_axis"})
    assert resp7.status_code == 422, f"expected 422, got {resp7.status_code}"

    # --- Test limit ---
    resp8 = client.get("/api/axis-score-ranking", params={"limit": 1})
    assert resp8.status_code == 200
    assert len(resp8.json()["ranked_servers"]) == 1

    print("PASS")
    _sys.exit(0)
