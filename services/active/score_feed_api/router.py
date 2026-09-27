# deps: fastapi, pydantic, sqlalchemy
"""Score Feed API -- public endpoint for MCP server scores and risk tiers.

Surfaces data from APP tables (mcp_llm_axis_scores, mcp_server_registry).
Auth is public per directive config.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/score_feed", tags=["score_feed_api"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class AxisScoreItem(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    model_version: str
    scored_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ServerScoreDetail(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    model_version: Optional[str] = None
    overall_risk_label: Optional[str] = None
    axes: list[AxisScoreItem] = Field(default_factory=list)
    last_scored: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ServerSummaryItem(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    overall_risk_label: Optional[str] = None
    last_scored: Optional[str] = None


class ServerListResponse(BaseModel):
    servers: list[ServerSummaryItem]
    total: int
    offset: int
    limit: int


class RiskBucket(BaseModel):
    label: str
    count: int
    pct: float


class DistributionResponse(BaseModel):
    total_servers: int
    scored_servers: int
    unscored_servers: int
    risk_buckets: list[RiskBucket]
    generated_at: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _latest_model_version(db: Session) -> Optional[str]:
    row = (
        db.execute(
            select(McpLlmAxisScore.model_version)
            .where(McpLlmAxisScore.axis_name == "overall_risk")
            .order_by(McpLlmAxisScore.scored_at.desc())
            .limit(1)
        )
        .scalar_one_or_none()
    )
    return row


def _risk_label_from_row(row: McpLlmAxisScore) -> Optional[str]:
    return row.label if row else None


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/servers", response_model=ServerListResponse)
def list_servers(
    risk_tier: Optional[str] = Query(None, description="Filter by risk tier (LOW/MEDIUM/HIGH/CRITICAL)"),
    source: Optional[str] = Query(None, description="Filter by registry source"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
) -> ServerListResponse:
    """List MCP servers with optional filters and pagination.
    Each server includes the latest overall_risk label from the current model version.
    """
    mv = _latest_model_version(db)

    # Base query for servers
    srv_q = select(McpServerRegistry)
    conds = []

    if source:
        conds.append(McpServerRegistry.registry_source == source)

    if risk_tier:
        conds.append(McpServerRegistry.risk_tier == risk_tier.upper())

    if conds:
        srv_q = srv_q.where(and_(*conds))

    total = db.execute(select(func.count()).select_from(srv_q.subquery())).scalar_one() or 0

    srv_q = (
        srv_q
        .order_by(McpServerRegistry.name)
        .offset(offset)
        .limit(limit)
    )
    servers = db.execute(srv_q).scalars().all()

    if not servers:
        return ServerListResponse(servers=[], total=total, offset=offset, limit=limit)

    server_ids = [s.server_id for s in servers]

    # Fetch latest overall_risk label per server
    overall_q = (
        select(McpLlmAxisScore.server_id, McpLlmAxisScore.label, McpLlmAxisScore.scored_at)
        .where(
            McpLlmAxisScore.server_id.in_(server_ids),
            McpLlmAxisScore.axis_name == "overall_risk",
        )
    )
    if mv:
        overall_q = overall_q.where(McpLlmAxisScore.model_version == mv)
    overall_q = overall_q.order_by(McpLlmAxisScore.scored_at.desc())

    overall_rows = db.execute(overall_q).all()
    # Deduplicate to latest per server
    overall_map: dict[str, tuple] = {}
    for row in overall_rows:
        sid = row[0]
        if sid not in overall_map:
            overall_map[sid] = (row[1], row[2])

    items = []
    for srv in servers:
        label, scored_at = overall_map.get(srv.server_id, (None, None))
        scored_str = None
        if scored_at:
            if isinstance(scored_at, datetime):
                scored_str = scored_at.isoformat()
            else:
                scored_str = str(scored_at)
        items.append(
            ServerSummaryItem(
                server_id=srv.server_id,
                name=srv.name,
                url=srv.url,
                registry_source=srv.registry_source,
                risk_tier=srv.risk_tier,
                overall_risk_label=label,
                last_scored=scored_str,
            )
        )

    return ServerListResponse(servers=items, total=total, offset=offset, limit=limit)


@router.get("/server/{server_id}", response_model=ServerScoreDetail)
def get_server_scores(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerScoreDetail:
    """Return all axis scores for a server with registry metadata."""
    reg = db.get(McpServerRegistry, server_id)
    if not reg:
        raise HTTPException(status_code=404, detail=f"Server {server_id!r} not found")

    mv = _latest_model_version(db)
    axes_q = select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    if mv:
        axes_q = axes_q.where(McpLlmAxisScore.model_version == mv)
    axes_q = axes_q.order_by(McpLlmAxisScore.axis_name)
    axes_rows = db.execute(axes_q).scalars().all()

    if not axes_rows:
        raise HTTPException(status_code=404, detail=f"No scores found for server {server_id!r}")

    overall_label: Optional[str] = None
    latest_scored: Optional[datetime] = None
    axes_items = []
    for r in axes_rows:
        if r.axis_name == "overall_risk":
            overall_label = r.label
            latest_scored = r.scored_at
        scored_str: Optional[str] = None
        if r.scored_at:
            if isinstance(r.scored_at, datetime):
                scored_str = r.scored_at.isoformat()
            else:
                scored_str = str(r.scored_at)
        axes_items.append(
            AxisScoreItem(
                axis_name=r.axis_name,
                label=r.label,
                label_index=r.label_index,
                p_top=r.p_top,
                p_critical=r.p_critical,
                p_danger=r.p_danger,
                model_version=r.model_version,
                scored_at=scored_str,
            )
        )

    return ServerScoreDetail(
        server_id=server_id,
        name=reg.name,
        url=reg.url,
        registry_source=reg.registry_source,
        risk_tier=reg.risk_tier,
        model_version=mv,
        overall_risk_label=overall_label,
        axes=axes_items,
        last_scored=latest_scored.isoformat() if latest_scored else None,
    )


@router.get("/server/{server_id}/verdict", response_model=dict)
def get_server_verdict(
    server_id: str,
    db: Session = Depends(get_session),
) -> dict:
    """Return the 7-axis verdict dict for a server."""
    mv = _latest_model_version(db)
    axes_q = select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    if mv:
        axes_q = axes_q.where(McpLlmAxisScore.model_version == mv)
    axes_rows = db.execute(axes_q).scalars().all()

    if not axes_rows:
        raise HTTPException(status_code=404, detail=f"No verdict found for server {server_id!r}")

    return {
        "server_id": server_id,
        "model_version": mv,
        "axes": {r.axis_name: r.label for r in axes_rows},
        "scored_at": axes_rows[0].scored_at.isoformat() if axes_rows and axes_rows[0].scored_at else None,
    }


@router.get("/distribution", response_model=DistributionResponse)
def get_score_distribution(
    days: int = Query(30, ge=1, le=365, description="Lookback window in days"),
    db: Session = Depends(get_session),
) -> DistributionResponse:
    """Return risk-tier distribution across all scored servers."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    total = db.execute(select(func.count(McpServerRegistry.server_id))).scalar_one() or 0

    scored_ids_q = (
        select(McpLlmAxisScore.server_id)
        .where(
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .distinct()
    )
    mv = _latest_model_version(db)
    if mv:
        scored_ids_q = scored_ids_q.where(McpLlmAxisScore.model_version == mv)

    scored_ids = {r for r in db.execute(scored_ids_q).scalars().all()}
    scored_count = len(scored_ids)
    unscored_count = total - scored_count

    # Risk bucket counts from axis scores
    bucket_q = (
        select(McpLlmAxisScore.label, func.count())
        .where(
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.scored_at >= cutoff,
        )
    )
    if mv:
        bucket_q = bucket_q.where(McpLlmAxisScore.model_version == mv)
    bucket_q = bucket_q.group_by(McpLlmAxisScore.label)

    rows = db.execute(bucket_q).all()
    label_counts = {r[0] or "UNKNOWN": r[1] for r in rows}

    bucket_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    buckets = []
    for label in bucket_order:
        cnt = label_counts.get(label, 0)
        pct = round(cnt / scored_count, 4) if scored_count > 0 else 0.0
        buckets.append(RiskBucket(label=label, count=cnt, pct=pct))

    # Catch-all for other labels
    known = set(label_counts.keys()) & set(bucket_order)
    unknown_total = sum(v for k, v in label_counts.items() if k not in known)
    if unknown_total > 0:
        pct = round(unknown_total / scored_count, 4) if scored_count > 0 else 0.0
        buckets.append(RiskBucket(label="UNKNOWN", count=unknown_total, pct=pct))

    return DistributionResponse(
        total_servers=total,
        scored_servers=scored_count,
        unscored_servers=unscored_count,
        risk_buckets=buckets,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
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
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override

    client = TestClient(test_app)

    # Seed data
    with TestSession() as db:
        now = datetime.now(timezone.utc)
        db.add(McpServerRegistry(
            server_id="feed-srv-1", name="Feed Test A",
            registry_source="npm", risk_tier="LOW", url="https://example.com/a",
        ))
        db.add(McpServerRegistry(
            server_id="feed-srv-2", name="Feed Test B",
            registry_source="github", risk_tier="HIGH", url="https://example.com/b",
        ))
        db.add(McpServerRegistry(
            server_id="feed-srv-3", name="Feed Test C",
            registry_source="npm", risk_tier=None,
        ))
        db.flush()
        _idx_map = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3, "STRONG": 0, "WEAK": 1}
        _id_counter = [0]
        for sid, axis, lbl, p_top, mv in [
            ("feed-srv-1", "overall_risk", "LOW", 0.85, "feed-v1"),
            ("feed-srv-1", "auth_strength", "STRONG", 0.9, "feed-v1"),
            ("feed-srv-2", "overall_risk", "HIGH", 0.3, "feed-v1"),
            ("feed-srv-2", "auth_strength", "WEAK", 0.2, "feed-v1"),
        ]:
            _id_counter[0] += 1
            db.add(McpLlmAxisScore(
                id=_id_counter[0],
                server_id=sid, axis_name=axis, label=lbl,
                label_index=_idx_map.get(lbl, 0),
                p_top=p_top, p_critical=1 - p_top, p_danger=0.0,
                model_version=mv, scored_at=now,
                adapter_sha256="dummysha",
                probs={},
                escalated=False,
                escalated_to=None,
                decision_rule_version="feed-v1",
            ))
        db.commit()

    # --- list_servers happy path ---
    r = client.get("/api/score_feed/servers")
    assert r.status_code == 200, f"list_servers failed: {r.text}"
    d = r.json()
    assert d["total"] >= 3, d["total"]
    assert len(d["servers"]) == 3, len(d["servers"])

    # --- list_servers with source filter ---
    r2 = client.get("/api/score_feed/servers?source=npm")
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    assert all(s["registry_source"] == "npm" for s in d2["servers"])

    # --- list_servers with pagination ---
    r3 = client.get("/api/score_feed/servers?limit=2&offset=0")
    assert r3.status_code == 200, r3.text
    d3 = r3.json()
    assert len(d3["servers"]) == 2, len(d3["servers"])
    assert d3["offset"] == 0
    assert d3["limit"] == 2

    # --- get_server_scores happy path ---
    r4 = client.get("/api/score_feed/server/feed-srv-1")
    assert r4.status_code == 200, f"get_server_scores failed: {r4.text}"
    d4 = r4.json()
    assert d4["server_id"] == "feed-srv-1"
    assert d4["name"] == "Feed Test A"
    assert d4["overall_risk_label"] == "LOW", d4["overall_risk_label"]
    assert len(d4["axes"]) == 2, len(d4["axes"])
    axis_names = {a["axis_name"] for a in d4["axes"]}
    assert "overall_risk" in axis_names

    # --- get_server_scores 404 ---
    r5 = client.get("/api/score_feed/server/no-such-server")
    assert r5.status_code == 404, f"expected 404, got {r5.status_code}"

    # --- get_server_verdict ---
    r6 = client.get("/api/score_feed/server/feed-srv-2/verdict")
    assert r6.status_code == 200, r6.text
    d6 = r6.json()
    assert "axes" in d6
    assert d6["axes"].get("overall_risk") == "HIGH", d6["axes"]

    # --- get_score_distribution ---
    r7 = client.get("/api/score_feed/distribution?days=30")
    assert r7.status_code == 200, f"distribution failed: {r7.text}"
    d7 = r7.json()
    assert "risk_buckets" in d7
    assert d7["total_servers"] >= 3
    assert d7["scored_servers"] >= 2
    labels = {b["label"] for b in d7["risk_buckets"]}
    assert "LOW" in labels
    assert "HIGH" in labels

    # --- validation failure ---
    r8 = client.get("/api/score_feed/servers?limit=0")
    assert r8.status_code == 422, f"expected 422 for limit=0, got {r8.status_code}"

    print("PASS")
    sys.exit(0)
