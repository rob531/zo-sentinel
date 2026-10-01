# deps: fastapi, sqlalchemy
"""Scoring Delta Timeline Service.

GET /api/scoring-delta-timeline
  Returns per-axis label changes between consecutive score evaluations,
  ranked by delta magnitude (non-zero changes first).  Reads from
  mcp_llm_axis_scores via the app.db SQLAlchemy session.

Public endpoint — no auth required (PRODUCT_SPEC §9 scope).
"""
from __future__ import annotations

import os
import sys

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["scoring_delta_timeline"])

# --------------------------------------------------------------------------- #
# Risk ordering for trajectory determination
# --------------------------------------------------------------------------- #
_RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _determine_trajectory(prev_label: str, cur_label: str) -> str:
    prev_val = _RISK_ORDER.get(prev_label.upper(), -1)
    cur_val = _RISK_ORDER.get(cur_label.upper(), -1)
    if prev_val == -1 or cur_val == -1:
        return "STABLE"
    if cur_val > prev_val:
        return "DEGRADED"
    if cur_val < prev_val:
        return "IMPROVED"
    return "STABLE"


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class DeltaRecord(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    axis_name: str
    label: str = Field(..., description="Most-recent label for this axis")
    scored_at: str = Field(..., description="ISO-8601 timestamp of the most-recent score")
    from_label: str = Field(..., description="Previous label")
    to_label: str = Field(..., description="Current label (= label)")
    trajectory: str = Field(..., description="IMPROVED | DEGRADED | STABLE")
    delta: float = Field(..., description="p_top delta (current - previous)")


class ScoringDeltaTimelineResponse(BaseModel):
    server_id: Optional[str] = Field(default=None, description="Server ID if filtered, else None (all servers)")
    days: int = Field(..., description="Look-back window in days")
    total_deltas: int = Field(..., description="Number of delta records returned")
    deltas: list[DeltaRecord] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
@router.get(
    "/scoring-delta-timeline",
    response_model=ScoringDeltaTimelineResponse,
    summary="Get per-axis score deltas between consecutive evaluations",
)
def scoring_delta_timeline(
    limit: int = Query(default=50, ge=1, le=500, description="Max records to return"),
    server_id: Optional[str] = Query(default=None, description="Filter to a specific server (optional)"),
    days: int = Query(default=30, ge=1, le=365, description="Look-back window in days"),
    db: Session = Depends(get_session),
) -> ScoringDeltaTimelineResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)

    stmt = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.scored_at,
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.scored_at.desc(),
        )
    )
    if server_id is not None:
        stmt = stmt.where(McpLlmAxisScore.server_id == server_id)

    rows = db.execute(stmt).all()

    if not rows:
        raise HTTPException(status_code=404, detail="No scoring data found for the given parameters")

    server_ids_in_result = list({r.server_id for r in rows})
    name_rows = db.execute(
        select(McpServerRegistry.server_id, McpServerRegistry.name).where(
            McpServerRegistry.server_id.in_(server_ids_in_result)
        )
    ).all()
    server_name_map = {row.server_id: row.name for row in name_rows}

    groups: dict[tuple[str, str], list] = defaultdict(list)
    for row in rows:
        groups[(row.server_id, row.axis_name)].append(row)

    deltas: list[DeltaRecord] = []
    for (sid, axis), recs in groups.items():
        recent = recs[:20]
        for i in range(len(recent) - 1):
            cur = recent[i]
            prev = recent[i + 1]
            from_label = prev.label or ""
            to_label = cur.label or ""
            trajectory = _determine_trajectory(from_label, to_label)
            p_cur = cur.p_top if cur.p_top is not None else 0.0
            p_prev = prev.p_top if prev.p_top is not None else 0.0
            delta_val = p_cur - p_prev
            scored_ts = cur.scored_at
            deltas.append(
                DeltaRecord(
                    server_id=sid,
                    server_name=server_name_map.get(sid),
                    axis_name=axis,
                    label=to_label,
                    scored_at=scored_ts.isoformat() if isinstance(scored_ts, datetime) else str(scored_ts),
                    from_label=from_label,
                    to_label=to_label,
                    trajectory=trajectory,
                    delta=round(delta_val, 6),
                )
            )

    deltas.sort(key=lambda d: (0 if d.trajectory != "STABLE" else 1, d.scored_at), reverse=True)
    return ScoringDeltaTimelineResponse(
        server_id=server_id,
        days=days,
        total_deltas=len(deltas),
        deltas=deltas[:limit],
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Ensure repo root is on sys.path so `app` imports resolve
    _repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override

    now = datetime.utcnow()

    with TestSession() as sess:
        sess.add(McpServerRegistry(server_id="srv1", name="Server One"))
        sess.add(McpServerRegistry(server_id="srv2", name="Server Two"))
        sess.add_all([
            # srv1 / overall_risk: HIGH (day-1) vs MEDIUM (day-2) → DEGRADED
            McpLlmAxisScore(
                server_id="srv1", axis_name="overall_risk",
                label="MEDIUM", p_top=0.45,
                model_version="v1", scored_at=now - timedelta(days=2),
                adapter_sha256="a" * 64, decision_rule_version="v1",
                probs="{}", p_critical=0.1, p_danger=0.2,
                escalated=False, label_index=1,
            ),
            McpLlmAxisScore(
                server_id="srv1", axis_name="overall_risk",
                label="HIGH", p_top=0.65,
                model_version="v1", scored_at=now - timedelta(days=1),
                adapter_sha256="b" * 64, decision_rule_version="v1",
                probs="{}", p_critical=0.2, p_danger=0.3,
                escalated=False, label_index=2,
            ),
            # srv1 / auth_strength: STRONG (day-1) vs WEAK (day-2) → IMPROVED
            McpLlmAxisScore(
                server_id="srv1", axis_name="auth_strength",
                label="WEAK", p_top=0.25,
                model_version="v1", scored_at=now - timedelta(days=2),
                adapter_sha256="c" * 64, decision_rule_version="v1",
                probs="{}", p_critical=0.05, p_danger=0.1,
                escalated=False, label_index=0,
            ),
            McpLlmAxisScore(
                server_id="srv1", axis_name="auth_strength",
                label="STRONG", p_top=0.85,
                model_version="v1", scored_at=now - timedelta(days=1),
                adapter_sha256="d" * 64, decision_rule_version="v1",
                probs="{}", p_critical=0.05, p_danger=0.05,
                escalated=False, label_index=3,
            ),
            # srv2 / overall_risk: LOW then LOW again → STABLE
            McpLlmAxisScore(
                server_id="srv2", axis_name="overall_risk",
                label="LOW", p_top=0.15,
                model_version="v1", scored_at=now - timedelta(days=2),
                adapter_sha256="e" * 64, decision_rule_version="v1",
                probs="{}", p_critical=0.0, p_danger=0.05,
                escalated=False, label_index=0,
            ),
            McpLlmAxisScore(
                server_id="srv2", axis_name="overall_risk",
                label="LOW", p_top=0.15,
                model_version="v1", scored_at=now - timedelta(days=1),
                adapter_sha256="f" * 64, decision_rule_version="v1",
                probs="{}", p_critical=0.0, p_danger=0.05,
                escalated=False, label_index=0,
            ),
        ])
        sess.commit()

    client = TestClient(test_app)

    # Test 1: happy path
    r = client.get("/api/scoring-delta-timeline?limit=10&days=7")
    if r.status_code != 200:
        print(f"FAIL: expected 200, got {r.status_code}: {r.text}")
        sys.exit(1)
    d = r.json()
    if d.get("days") != 7:
        print(f"FAIL: days mismatch: {d.get('days')}")
        sys.exit(1)
    if d.get("total_deltas", 0) < 3:
        print(f"FAIL: expected >=3 deltas, got {d.get('total_deltas')}")
        sys.exit(1)
    for rec in d.get("deltas", []):
        if rec["trajectory"] not in {"IMPROVED", "DEGRADED", "STABLE"}:
            print(f"FAIL: bad trajectory {rec['trajectory']}")
            sys.exit(1)

    # Test 2: server_id filter
    r2 = client.get("/api/scoring-delta-timeline?server_id=srv1&days=7")
    if r2.status_code != 200:
        print(f"FAIL: filtered request returned {r2.status_code}: {r2.text}")
        sys.exit(1)
    d2 = r2.json()
    if d2.get("server_id") != "srv1":
        print(f"FAIL: server_id filter not reflected: {d2}")
        sys.exit(1)
    srv1_axes = {rec["axis_name"] for rec in d2.get("deltas", [])}
    if len(srv1_axes) != 2:
        print(f"FAIL: expected 2 axes for srv1, got {srv1_axes}")
        sys.exit(1)

    # Test 3: limit respected
    r3 = client.get("/api/scoring-delta-timeline?limit=1&days=7")
    if r3.status_code != 200:
        print(f"FAIL: limit=1 returned {r3.status_code}")
        sys.exit(1)
    d3 = r3.json()
    if len(d3.get("deltas", [])) > 1:
        print(f"FAIL: limit=1 not respected, got {len(d3['deltas'])}")
        sys.exit(1)

    # Test 4: 404 for no matching data
    r4 = client.get("/api/scoring-delta-timeline?server_id=nonexistent&days=7")
    if r4.status_code != 404:
        print(f"FAIL: nonexistent server should 404, got {r4.status_code}")
        sys.exit(1)

    # Test 5: trajectory correctness
    trajectories = {rec["axis_name"]: rec["trajectory"] for rec in d.get("deltas", [])}
    if trajectories.get("overall_risk") != "DEGRADED":
        print(f"FAIL: overall_risk should be DEGRADED, got {trajectories.get('overall_risk')}")
        sys.exit(1)
    if trajectories.get("auth_strength") != "IMPROVED":
        print(f"FAIL: auth_strength should be IMPROVED, got {trajectories.get('auth_strength')}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)
