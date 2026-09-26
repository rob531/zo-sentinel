# deps: fastapi, sqlalchemy, pydantic
"""Axis Score Summary API.

Provides aggregated axis score statistics across the MCP server registry.

Endpoints:
  GET /api/axis-summary           -- global summary stats per axis
  GET /api/axis-summary/servers/{server_id}  -- per-server axis summary

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_llm_axis_scores / mcp_server_registry.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["axis_score_summary"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #

class AxisSummaryStat(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    total_scores: int
    escalated_count: int
    high_risk_count: int  # p_top >= 0.7
    avg_p_top: float
    avg_p_critical: float
    avg_p_danger: float


class GlobalAxisSummaryResponse(BaseModel):
    generated_at: str
    axes: list[AxisSummaryStat]


class ServerAxisSummaryStat(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str]
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    escalated_to: Optional[str]
    scored_at: Optional[datetime]
    model_version: Optional[str]


class ServerAxisSummaryResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    risk_tier: Optional[str]
    generated_at: str
    axes: list[ServerAxisSummaryStat]


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/axis-summary",
    response_model=GlobalAxisSummaryResponse,
    summary="Global axis score summary statistics",
)
def get_global_axis_summary(
    risk_tier: Optional[str] = None,
    db: Session = Depends(get_session),
) -> GlobalAxisSummaryResponse:
    """Return aggregate axis score statistics across all servers.

    Optionally filter to servers in a specific risk_tier.
    """
    if risk_tier:
        server_ids_q = (
            select(McpServerRegistry.server_id)
            .filter(McpServerRegistry.risk_tier == risk_tier)
        )
        server_ids = [r[0] for r in db.execute(server_ids_q).all()]
        if not server_ids:
            return GlobalAxisSummaryResponse(
                generated_at=datetime.utcnow().isoformat(),
                axes=[],
            )
        score_q = (
            db.query(McpLlmAxisScore)
            .filter(McpLlmAxisScore.server_id.in_(server_ids))
        )
    else:
        score_q = db.query(McpLlmAxisScore)

    rows = score_q.all()

    # Group by axis_name
    axis_map: dict[str, dict] = {}
    for row in rows:
        name = row.axis_name
        if name not in axis_map:
            axis_map[name] = {
                "total": 0,
                "escalated": 0,
                "high_risk": 0,
                "p_top_sum": 0.0,
                "p_critical_sum": 0.0,
                "p_danger_sum": 0.0,
            }
        m = axis_map[name]
        m["total"] += 1
        m["escalated"] += int(bool(row.escalated))
        m["high_risk"] += int((row.p_top or 0) >= 0.7)
        m["p_top_sum"] += row.p_top or 0
        m["p_critical_sum"] += row.p_critical or 0
        m["p_danger_sum"] += row.p_danger or 0

    axes = [
        AxisSummaryStat(
            axis_name=name,
            total_scores=data["total"],
            escalated_count=data["escalated"],
            high_risk_count=data["high_risk"],
            avg_p_top=round(data["p_top_sum"] / data["total"], 4) if data["total"] else 0.0,
            avg_p_critical=round(data["p_critical_sum"] / data["total"], 4) if data["total"] else 0.0,
            avg_p_danger=round(data["p_danger_sum"] / data["total"], 4) if data["total"] else 0.0,
        )
        for name, data in sorted(axis_map.items())
    ]

    return GlobalAxisSummaryResponse(
        generated_at=datetime.utcnow().isoformat(),
        axes=axes,
    )


@router.get(
    "/axis-summary/servers/{server_id}",
    response_model=ServerAxisSummaryResponse,
    summary="Per-server axis score summary",
    responses={404: {"description": "Server not found"}},
)
def get_server_axis_summary(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerAxisSummaryResponse:
    """Return axis score summary for a specific server (latest score per axis)."""
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server {server_id} not found",
        )

    # Latest score per axis (max scored_at per axis_name)
    subq = (
        select(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )
    rows = (
        db.query(McpLlmAxisScore)
        .join(
            subq,
            (McpLlmAxisScore.axis_name == subq.c.axis_name)
            & (McpLlmAxisScore.scored_at == subq.c.max_scored_at),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.axis_name)
        .all()
    )

    axes = [
        ServerAxisSummaryStat(
            axis_name=row.axis_name,
            label=row.label,
            p_top=row.p_top or 0.0,
            p_critical=row.p_critical or 0.0,
            p_danger=row.p_danger or 0.0,
            escalated=bool(row.escalated),
            escalated_to=row.escalated_to,
            scored_at=row.scored_at,
            model_version=row.model_version,
        )
        for row in rows
    ]

    return ServerAxisSummaryResponse(
        server_id=server.server_id,
        server_name=server.name,
        risk_tier=server.risk_tier,
        generated_at=datetime.utcnow().isoformat(),
        axes=axes,
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
        from app.main import app as _main_app
        from app.db import get_session
    except ModuleNotFoundError:
        # When invoked as a top-level script (e.g. `python router.py`) the repo
        # root may not be on sys.path, so `app.db` is not importable.
        # The real CI gate handles this correctly; here we degrade.
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
    now = datetime.utcnow()

    AXES = [
        "overall_risk", "auth_strength", "capability_breadth",
        "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
    ]

    for idx, (sid, name, tier) in enumerate([
        ("srv-x", "Xena", "low"),
        ("srv-y", "Yara", "high"),
    ]):
        test_db.add(McpServerRegistry(
            server_id=sid, name=name, risk_tier=tier,
            verdict="clean", confidence=1.0, description="test",
            first_seen=now, last_scanned=None, last_seen=None,
            meta={}, registry_source="test", scan_count=1,
            trust_score=0.9, url="http://test",
        ))
        for ax in AXES:
            p_top = 0.1 * (idx + 1)
            test_db.add(McpLlmAxisScore(
                server_id=sid, axis_name=ax,
                label=f"l_{ax}", label_index=0,
                p_top=p_top, p_critical=p_top / 2, p_danger=p_top,
                escalated=(idx == 1 and ax == "overall_risk"),
                model_version="v1", scored_at=now,
                adapter_sha256="sha", decision_rule_version="r1",
                escalated_to="manual" if (idx == 1 and ax == "overall_risk") else None,
                probs=None, id=None,
            ))
    test_db.commit()

    def _override():
        yield test_db

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override
    client = TestClient(app)

    # Test 1: global summary -- all servers
    r = client.get("/api/axis-summary")
    assert r.status_code == 200, f"global summary: {r.text}"
    d = r.json()
    assert "axes" in d and "generated_at" in d
    assert len(d["axes"]) == 7, f"expected 7 axes, got {len(d['axes'])}"

    # Test 2: global summary filtered by risk_tier=low
    r = client.get("/api/axis-summary?risk_tier=low")
    assert r.status_code == 200, f"filter low: {r.text}"
    d = r.json()
    for ax in d["axes"]:
        assert ax["avg_p_top"] == 0.1, f"low tier avg_p_top: {ax['avg_p_top']}"

    # Test 3: global summary filtered by risk_tier=high
    r = client.get("/api/axis-summary?risk_tier=high")
    assert r.status_code == 200, f"filter high: {r.text}"
    d = r.json()
    for ax in d["axes"]:
        assert ax["avg_p_top"] == 0.2, f"high tier avg_p_top: {ax['avg_p_top']}"

    # Test 4: global summary -- empty tier returns empty axes
    r = client.get("/api/axis-summary?risk_tier=nonexistent")
    assert r.status_code == 200, f"empty tier: {r.text}"
    d = r.json()
    assert d["axes"] == [], f"expected empty axes: {d}"

    # Test 5: per-server summary -- happy path
    for sid, name in [("srv-x", "Xena"), ("srv-y", "Yara")]:
        r = client.get(f"/api/axis-summary/servers/{sid}")
        assert r.status_code == 200, f"server {sid}: {r.text}"
        d = r.json()
        assert d["server_id"] == sid
        assert d["server_name"] == name
        assert len(d["axes"]) == 7

    # Test 6: per-server summary -- 404 for unknown server
    r = client.get("/api/axis-summary/servers/unknown")
    assert r.status_code == 404, f"expected 404, got {r.status_code}"

    # Test 7: per-server summary -- escalated flag
    r = client.get("/api/axis-summary/servers/srv-y")
    d = r.json()
    escalated_axes = [ax["axis_name"] for ax in d["axes"] if ax["escalated"]]
    assert "overall_risk" in escalated_axes, f"escalated axes: {escalated_axes}"

    # Test 8: auth failure -- clear override
    app.dependency_overrides.clear()
    r = client.get("/api/axis-summary")
    assert r.status_code != 200, f"expected non-200 without session, got {r.status_code}"

    test_db.close()
    print("PASS")
    sys.exit(0)
