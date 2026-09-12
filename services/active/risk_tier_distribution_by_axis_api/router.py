# deps: fastapi, pydantic, sqlalchemy
"""router.py -- risk tier distribution broken down by LLM scoring axis.

GET /api/risk/distribution-by-axis
  Returns count of servers per risk tier, grouped by axis_name.
  Risk tier is derived from p_top/p_critical probability columns on McpLlmAxisScore.

Auth: public.
Data: app tier via get_session + SQLAlchemy ORM on mcp_llm_axis_scores /
  mcp_server_registry.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_tier_distribution_by_axis_api"])


# --------------------------------------------------------------------------- #
# Risk-tier derivation (must match the scoring logic in the pipeline)
# --------------------------------------------------------------------------- #

def _risk_tier(p_top: Optional[float], p_critical: Optional[float]) -> str:
    """Derive risk tier label from probability columns."""
    if p_critical is not None and p_critical >= 0.5:
        return "CRITICAL"
    if p_top is not None and p_top >= 0.7:
        return "HIGH"
    if p_top is not None and p_top >= 0.4:
        return "MEDIUM"
    return "LOW"


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class TierBucket(BaseModel):
    tier: str
    count: int
    pct: float = Field(..., ge=0.0, le=100.0)


class AxisBreakdown(BaseModel):
    axis_name: str
    total_servers: int
    tiers: List[TierBucket]


class DistributionResponse(BaseModel):
    generated_at: str
    axes: List[AxisBreakdown]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _pct(count: int, total: int) -> float:
    if total == 0:
        return 0.0
    return round((count / total) * 100, 2)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/risk/distribution-by-axis",
    response_model=DistributionResponse,
    name="risk_tier_distribution_by_axis:overview",
)
def get_distribution_by_axis(
    axis_name: Optional[str] = Query(
        None,
        description="Filter to a specific axis (e.g. 'overall_risk', 'auth_strength'). "
                    "Omit to get all axes.",
    ),
    db: Session = Depends(get_session),
) -> DistributionResponse:
    """
    Return the count of servers in each risk tier, broken down by LLM axis.

    Each axis shows the distribution of servers across CRITICAL / HIGH / MEDIUM /
    LOW risk tiers based on the latest axis scores.

    Tiers are derived from the p_top / p_critical probability columns:
      CRITICAL  p_critical >= 0.5
      HIGH      p_top >= 0.7
      MEDIUM    p_top >= 0.4
      LOW       otherwise
    """
    now = datetime.now(timezone.utc).isoformat()

    # Build the tier label in SQL so we can GROUP BY it.
    tier_expr = case(
        (McpLlmAxisScore.p_critical >= 0.5, "CRITICAL"),
        (McpLlmAxisScore.p_top >= 0.7, "HIGH"),
        (McpLlmAxisScore.p_top >= 0.4, "MEDIUM"),
        else_="LOW",
    ).label("risk_tier")

    # Group by axis + tier and count distinct servers.
    agg_query = (
        select(
            McpLlmAxisScore.axis_name.label("axis"),
            tier_expr.label("tier"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("cnt"),
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
    )

    if axis_name:
        agg_query = agg_query.where(McpLlmAxisScore.axis_name == axis_name)

    agg_rows = (
        db.execute(
            agg_query.group_by(McpLlmAxisScore.axis_name, tier_expr).order_by(
                McpLlmAxisScore.axis_name,
                case(
                    (tier_expr == "CRITICAL", 1),
                    (tier_expr == "HIGH", 2),
                    (tier_expr == "MEDIUM", 3),
                    (tier_expr == "LOW", 4),
                    else_=5,
                ),
            )
        )
        .all()
    )

    # Compute totals per axis for percentage calculation.
    totals_query = (
        select(
            McpLlmAxisScore.axis_name.label("axis"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("total"),
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
    )
    if axis_name:
        totals_query = totals_query.where(McpLlmAxisScore.axis_name == axis_name)

    totals_rows = db.execute(totals_query.group_by(McpLlmAxisScore.axis_name)).all()
    totals_map: Dict[str, int] = {r.axis: r.total for r in totals_rows}

    # Collate into AxisBreakdown objects.
    axes_map: Dict[str, Dict] = {}
    for row in agg_rows:
        axis = row.axis
        tier = row.tier
        cnt = row.cnt
        total = totals_map.get(axis, 0)
        if axis not in axes_map:
            axes_map[axis] = {"axis_name": axis, "total_servers": total, "tiers": []}
        axes_map[axis]["tiers"].append(
            TierBucket(tier=tier, count=cnt, pct=_pct(cnt, total))
        )

    # Preserve axis ordering.
    ordered_axes = sorted(axes_map.keys())
    axes = [AxisBreakdown(**axes_map[k]) for k in ordered_axes]

    return DistributionResponse(generated_at=now, axes=axes)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path

    # Ensure repo root is on the path so `app` imports resolve.
    _repo = Path(__file__).resolve().parent.parent.parent  # services/active/.. -> repo
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base, McpServerRegistry, McpLlmAxisScore
    from app.db import get_session as _real_get_session

    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_engine)
    _TestSession = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

    # Seed data:
    # s1, s2 -> overall_risk CRITICAL (p_critical=0.6)
    # s1 -> auth_strength HIGH (p_top=0.75)
    # s3 -> overall_risk LOW (p_top=0.1)
    # s4 -> overall_risk MEDIUM (p_top=0.5)
    _servers = [
        McpServerRegistry(server_id="s1", name="Server 1", registry_source="npm"),
        McpServerRegistry(server_id="s2", name="Server 2", registry_source="npm"),
        McpServerRegistry(server_id="s3", name="Server 3", registry_source="github"),
        McpServerRegistry(server_id="s4", name="Server 4", registry_source="github"),
    ]

    _now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    _scores = [
        # s1 – CRITICAL overall_risk
        McpLlmAxisScore(
            server_id="s1", axis_name="overall_risk", label="CRITICAL",
            label_index=3, p_top=0.85, p_critical=0.6, p_danger=0.1,
            model_version="v1", scored_at=_now,
        ),
        # s1 – HIGH auth_strength
        McpLlmAxisScore(
            server_id="s1", axis_name="auth_strength", label="HIGH",
            label_index=2, p_top=0.75, p_critical=0.1, p_danger=0.05,
            model_version="v1", scored_at=_now,
        ),
        # s2 – CRITICAL overall_risk
        McpLlmAxisScore(
            server_id="s2", axis_name="overall_risk", label="CRITICAL",
            label_index=3, p_top=0.9, p_critical=0.7, p_danger=0.05,
            model_version="v1", scored_at=_now,
        ),
        # s3 – LOW overall_risk
        McpLlmAxisScore(
            server_id="s3", axis_name="overall_risk", label="LOW",
            label_index=0, p_top=0.1, p_critical=0.0, p_danger=0.1,
            model_version="v1", scored_at=_now,
        ),
        # s4 – MEDIUM overall_risk
        McpLlmAxisScore(
            server_id="s4", axis_name="overall_risk", label="MEDIUM",
            label_index=1, p_top=0.5, p_critical=0.05, p_danger=0.3,
            model_version="v1", scored_at=_now,
        ),
    ]

    with _TestSession() as _sess:
        for _srv in _servers:
            _sess.add(_srv)
        for _sc in _scores:
            _sess.add(_sc)
        _sess.commit()

    # Override get_session for the FastAPI app.
    _app = FastAPI()
    _app.include_router(router)

    def _override():
        _sess = _TestSession()
        try:
            yield _sess
        finally:
            _sess.close()

    _app.dependency_overrides[_real_get_session] = _override
    _client = TestClient(_app)

    # ---- Happy path: all axes ----
    _resp = _client.get("/api/risk/distribution-by-axis")
    if _resp.status_code != 200:
        print(f"FAIL: status {_resp.status_code}: {_resp.text}")
        _sys.exit(1)
    _data = _resp.json()
    for _key in ("generated_at", "axes"):
        if _key not in _data:
            print(f"FAIL: missing key '{_key}': {_data}")
            _sys.exit(1)

    # Find overall_risk breakdown
    _or_axis = next((a for a in _data["axes"] if a["axis_name"] == "overall_risk"), None)
    if not _or_axis:
        print(f"FAIL: no overall_risk axis in response: {_data['axes']}")
        _sys.exit(1)
    _tier_counts = {t["tier"]: t["count"] for t in _or_axis["tiers"]}
    assert _tier_counts.get("CRITICAL") == 2, f"Expected 2 CRITICAL, got {_tier_counts}"
    assert _tier_counts.get("MEDIUM") == 1, f"Expected 1 MEDIUM, got {_tier_counts}"
    assert _tier_counts.get("LOW") == 1, f"Expected 1 LOW, got {_tier_counts}"
    assert _or_axis["total_servers"] == 4, f"Expected total 4, got {_or_axis['total_servers']}"

    # Find auth_strength breakdown
    _auth_axis = next((a for a in _data["axes"] if a["axis_name"] == "auth_strength"), None)
    if not _auth_axis:
        print(f"FAIL: no auth_strength axis: {_data['axes']}")
        _sys.exit(1)
    _auth_tiers = {t["tier"]: t["count"] for t in _auth_axis["tiers"]}
    assert _auth_tiers.get("HIGH") == 1, f"Expected 1 HIGH, got {_auth_tiers}"

    # ---- Filter by axis_name ----
    _resp2 = _client.get("/api/risk/distribution-by-axis", params={"axis_name": "overall_risk"})
    if _resp2.status_code != 200:
        print(f"FAIL: filtered request status {_resp2.status_code}: {_resp2.text}")
        _sys.exit(1)
    _data2 = _resp2.json()
    if len(_data2["axes"]) != 1:
        print(f"FAIL: expected 1 axis, got {len(_data2['axes'])}")
        _sys.exit(1)
    if _data2["axes"][0]["axis_name"] != "overall_risk":
        print(f"FAIL: wrong axis returned: {_data2}")
        _sys.exit(1)

    # ---- Auth/permission failure: no session override ----
    _app.dependency_overrides.clear()
    _resp3 = _client.get("/api/risk/distribution-by-axis")
    if _resp3.status_code == 200:
        print(f"FAIL: expected non-200 without session override, got {_resp3.status_code}")
        _sys.exit(1)

    print("PASS")
    _sys.exit(0)
