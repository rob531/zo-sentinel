# deps: fastapi, sqlalchemy, pydantic
"""Axis Score Heatmap API.

Returns an axis-score heatmap: a server × axis matrix where each cell
contains the p_top score and label for a server's latest axis evaluation.

Endpoints
---------
  GET /api/axis-score-heatmap
      Full server × axis matrix with per-cell score/label.
      Query params:
        days      (int, default 30, range 1-365)  — lookback window
        axis      (str, optional)                  — filter to one axis
        risk_tier (str, optional)                  — filter servers by tier

  GET /api/axis-score-heatmap/servers/{server_id}
      Axis-score row for a single server.

Auth: public (auth=public per the directive).
Data: app tier via get_session + SQLAlchemy ORM on mcp_llm_axis_scores.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["axis_score_heatmap_api"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #

class HeatmapCell(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: bool = False


class HeatmapRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    cells: Dict[str, HeatmapCell] = Field(default_factory=dict)


class AxisMetadata(BaseModel):
    axis_name: str
    label: Optional[str] = None


class AxisScoreHeatmapResponse(BaseModel):
    generated_at: str
    window_days: int
    axes: List[AxisMetadata]
    servers: List[HeatmapRow]
    total_servers: int


class ServerHeatmapResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    generated_at: str
    cells: Dict[str, HeatmapCell]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _latest_per_server_axis(
    rows: list[McpLlmAxisScore],
) -> Dict[str, Dict[str, McpLlmAxisScore]]:
    """Return {server_id: {axis_name: latest_McpLlmAxisScore}}."""
    out: Dict[str, Dict[str, McpLlmAxisScore]] = {}
    for row in rows:
        out.setdefault(row.server_id, {})
        existing = out[row.server_id].get(row.axis_name)
        if existing is None or row.scored_at > existing.scored_at:
            out[row.server_id][row.axis_name] = row
    return out


def _score_to_cell(score: Optional[McpLlmAxisScore]) -> HeatmapCell:
    if score is None:
        return HeatmapCell()
    return HeatmapCell(
        label=score.label,
        label_index=score.label_index,
        p_top=score.p_top,
        p_critical=score.p_critical,
        p_danger=score.p_danger,
        escalated=bool(score.escalated),
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/axis-score-heatmap",
    response_model=AxisScoreHeatmapResponse,
    name="axis_score_heatmap:heatmap",
)
def get_axis_score_heatmap(
    days: int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    axis: Optional[str] = Query(default=None, description="Filter to a specific axis"),
    risk_tier: Optional[str] = Query(default=None, description="Filter servers by risk tier"),
    db: Session = Depends(get_session),
) -> AxisScoreHeatmapResponse:
    """
    Return a server × axis heatmap: for each server, the latest score per axis
    within the lookback window, with per-cell p_top/label/escalation detail.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    # Build base query — join through latest subquery for each (server, axis)
    sub = (
        db.query(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
        .subquery()
    )

    q = db.query(McpLlmAxisScore).join(
        sub,
        (McpLlmAxisScore.server_id == sub.c.server_id)
        & (McpLlmAxisScore.axis_name == sub.c.axis_name)
        & (McpLlmAxisScore.scored_at == sub.c.max_scored_at),
    )

    if axis:
        q = q.filter(McpLlmAxisScore.axis_name == axis)

    rows = q.all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No scores found in the last {days} days")

    # Server metadata
    server_ids = list({r.server_id for r in rows})
    srv_rows = (
        db.query(McpServerRegistry.server_id, McpServerRegistry.name, McpServerRegistry.risk_tier)
        .filter(McpServerRegistry.server_id.in_(server_ids))
        .all()
    )
    srv_meta: Dict[str, tuple[Optional[str], Optional[str]]] = {
        s.server_id: (s.name, s.risk_tier) for s in srv_rows
    }

    if risk_tier:
        srv_ids_filtered = {
            sid for sid, (_, tier) in srv_meta.items() if tier == risk_tier
        }
        if not srv_ids_filtered:
            raise HTTPException(
                status_code=404,
                detail=f"No servers found with risk_tier={risk_tier}",
            )
        rows = [r for r in rows if r.server_id in srv_ids_filtered]
        srv_meta = {k: v for k, v in srv_meta.items() if k in srv_ids_filtered}

    # Collect axis order
    axis_order: list[str] = []
    seen_axes: set[str] = set()
    for r in rows:
        if r.axis_name not in seen_axes:
            seen_axes.add(r.axis_name)
            axis_order.append(r.axis_name)

    # Build heatmap rows
    latest_by_server = _latest_per_server_axis(rows)
    servers_out: List[HeatmapRow] = []
    for srv_id in sorted(srv_meta.keys()):
        name, tier = srv_meta[srv_id]
        cells: Dict[str, HeatmapCell] = {}
        for ax in axis_order:
            score = latest_by_server.get(srv_id, {}).get(ax)
            cells[ax] = _score_to_cell(score)
        servers_out.append(
            HeatmapRow(server_id=srv_id, name=name, risk_tier=tier, cells=cells)
        )

    axes_out = [
        AxisMetadata(axis_name=ax, label=rows[0].label if rows else None)
        for ax in axis_order
    ]

    return AxisScoreHeatmapResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        window_days=days,
        axes=axes_out,
        servers=servers_out,
        total_servers=len(servers_out),
    )


@router.get(
    "/axis-score-heatmap/servers/{server_id}",
    response_model=ServerHeatmapResponse,
    name="axis_score_heatmap:server",
)
def get_server_axis_heatmap(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    db: Session = Depends(get_session),
) -> ServerHeatmapResponse:
    """
    Return the axis-score row for a single server within the lookback window.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    srv = db.execute(
        select(McpServerRegistry.name, McpServerRegistry.risk_tier).where(
            McpServerRegistry.server_id == server_id
        )
    ).first()

    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    name, risk_tier = srv

    sub = (
        db.query(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )

    rows = (
        db.query(McpLlmAxisScore)
        .join(
            sub,
            (McpLlmAxisScore.axis_name == sub.c.axis_name)
            & (McpLlmAxisScore.scored_at == sub.c.max_scored_at),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No scores found for server {server_id} in the last {days} days",
        )

    latest_by_axis = _latest_per_server_axis(rows).get(server_id, {})
    cells: Dict[str, HeatmapCell] = {
        ax: _score_to_cell(latest_by_axis.get(ax)) for ax in latest_by_axis
    }

    return ServerHeatmapResponse(
        server_id=server_id,
        name=name,
        risk_tier=risk_tier,
        generated_at=datetime.now(timezone.utc).isoformat(),
        cells=cells,
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
    from app.db import get_session as _real_get_session

    _app = FastAPI()
    _app.include_router(router)

    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_engine)
    _TestSession = sessionmaker(bind=_engine)
    _test_db = _TestSession()

    _now = datetime.utcnow()

    # Seed servers
    _test_db.add(McpServerRegistry(server_id="hm-srv-1", name="Heatmap Server Alpha", risk_tier="HIGH"))
    _test_db.add(McpServerRegistry(server_id="hm-srv-2", name="Heatmap Server Beta", risk_tier="MEDIUM"))
    _test_db.add(McpServerRegistry(server_id="hm-srv-3", name="Heatmap Server Gamma", risk_tier="LOW"))

    # Seed axis scores for hm-srv-1 (two axes)
    _test_db.add(McpLlmAxisScore(
        server_id="hm-srv-1", axis_name="overall_risk",
        label="HIGH", label_index=3, p_top=0.82, p_critical=0.40, p_danger=0.30,
        model_version="v1", decision_rule_version="v1", adapter_sha256="sha",
        scored_at=_now, escalated=False,
    ))
    _test_db.add(McpLlmAxisScore(
        server_id="hm-srv-1", axis_name="auth_strength",
        label="WEAK", label_index=0, p_top=0.10, p_critical=0.05, p_danger=0.20,
        model_version="v1", decision_rule_version="v1", adapter_sha256="sha",
        scored_at=_now, escalated=False,
    ))
    # hm-srv-2 (one axis)
    _test_db.add(McpLlmAxisScore(
        server_id="hm-srv-2", axis_name="overall_risk",
        label="MEDIUM", label_index=1, p_top=0.50, p_critical=0.20, p_danger=0.20,
        model_version="v1", decision_rule_version="v1", adapter_sha256="sha",
        scored_at=_now, escalated=False,
    ))
    _test_db.commit()

    def _override():
        try:
            yield _test_db
        finally:
            pass

    _app.dependency_overrides[_real_get_session] = _override
    _client = TestClient(_app)

    # Test 1: full heatmap happy path
    _resp = _client.get("/api/axis-score-heatmap?days=30")
    if _resp.status_code != 200:
        print(f"FAIL: heatmap status {_resp.status_code}: {_resp.text}")
        _sys.exit(1)
    _data = _resp.json()
    for _key in ("generated_at", "window_days", "axes", "servers", "total_servers"):
        if _key not in _data:
            print(f"FAIL: missing key '{_key}' in response")
            _sys.exit(1)
    if _data["total_servers"] != 3:
        print(f"FAIL: expected 3 servers, got {_data['total_servers']}")
        _sys.exit(1)
    if len(_data["axes"]) != 2:
        print(f"FAIL: expected 2 axes, got {len(_data['axes'])}")
        _sys.exit(1)
    for _srv in _data["servers"]:
        if "server_id" not in _srv or "cells" not in _srv:
            print(f"FAIL: malformed server row: {_srv}")
            _sys.exit(1)
        for _ax, _cell in _srv["cells"].items():
            for _f in ("label", "label_index", "p_top", "p_critical", "p_danger", "escalated"):
                if _f not in _cell:
                    print(f"FAIL: missing cell field '{_f}' in {_ax}: {_cell}")
                    _sys.exit(1)

    # Test 2: server detail endpoint
    _resp2 = _client.get("/api/axis-score-heatmap/servers/hm-srv-1?days=30")
    if _resp2.status_code != 200:
        print(f"FAIL: server detail status {_resp2.status_code}: {_resp2.text}")
        _sys.exit(1)
    _d2 = _resp2.json()
    if _d2["server_id"] != "hm-srv-1":
        print(f"FAIL: wrong server_id: {_d2}")
        _sys.exit(1)
    if len(_d2["cells"]) != 2:
        print(f"FAIL: expected 2 cells for hm-srv-1, got {len(_d2['cells'])}")
        _sys.exit(1)

    # Test 3: axis filter
    _resp3 = _client.get("/api/axis-score-heatmap?days=30&axis=overall_risk")
    if _resp3.status_code != 200:
        print(f"FAIL: axis filter status {_resp3.status_code}: {_resp3.text}")
        _sys.exit(1)
    if len(_resp3.json()["axes"]) != 1:
        print(f"FAIL: expected 1 axis after filter, got {len(_resp3.json()['axes'])}")
        _sys.exit(1)

    # Test 4: risk_tier filter
    _resp4 = _client.get("/api/axis-score-heatmap?days=30&risk_tier=HIGH")
    if _resp4.status_code != 200:
        print(f"FAIL: risk_tier filter status {_resp4.status_code}: {_resp4.text}")
        _sys.exit(1)
    if _resp4.json()["total_servers"] != 1:
        print(f"FAIL: expected 1 server with tier=HIGH, got {_resp4.json()['total_servers']}")
        _sys.exit(1)

    # Test 5: unknown server detail → 404
    _resp5 = _client.get("/api/axis-score-heatmap/servers/nobody?days=30")
    if _resp5.status_code != 404:
        print(f"FAIL: expected 404 for unknown server, got {_resp5.status_code}")
        _sys.exit(1)

    # Test 6: validation — days must be >= 1
    _resp6 = _client.get("/api/axis-score-heatmap?days=0")
    if _resp6.status_code != 422:
        print(f"FAIL: expected 422 for days=0, got {_resp6.status_code}")
        _sys.exit(1)

    print("PASS")
