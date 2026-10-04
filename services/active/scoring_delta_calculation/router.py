# deps: fastapi, pydantic, sqlalchemy
"""Scoring Delta Calculation Service.

GET /api/scoring-delta-calculation/{server_id}
  Computes per-axis p_top deltas and risk-tier changes between two snapshots
  of a server's axis scores (mcp_llm_axis_scores).  Reads from app Postgres
  via get_session + SQLAlchemy models.

Public endpoint — no auth required (PRODUCT_SPEC §9 scope).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["scoring_delta_calculation"])


# --------------------------------------------------------------------------- #
# Pydantic request / response models
# --------------------------------------------------------------------------- #
class AxisDelta(BaseModel):
    axis_name: str
    start_label: Optional[str] = None
    end_label: Optional[str] = None
    start_p_top: Optional[float] = None
    end_p_top: Optional[float] = None
    delta: float = 0.0


class ScoringDeltaCalculationResponse(BaseModel):
    server_id: str
    start_scored_at: str
    end_scored_at: str
    start_risk_tier: Optional[str] = None
    end_risk_tier: Optional[str] = None
    tier_changed: bool = False
    axes: list[AxisDelta] = Field(default_factory=list)
    overall_delta: float = 0.0


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
@router.get(
    "/scoring-delta-calculation/{server_id}",
    response_model=ScoringDeltaCalculationResponse,
    summary="Compute per-axis p_top deltas and risk-tier changes for a server",
)
def scoring_delta_calculation(
    server_id: str,
    start_date: datetime = Query(..., description="Start of window (ISO 8601)"),
    end_date: datetime = Query(..., description="End of window (ISO 8601)"),
    db: Session = Depends(lambda: None),  # placeholder; overridden by app
) -> ScoringDeltaCalculationResponse:
    # Deferred import so the module-level `from app.db import get_session`
    # only fires when the app is wiring the router (repo root is on PYTHONPATH
    # via uvicorn / the app bootstrap).  When run as __main__ we set up the
    # override before importing.
    from app.db import get_session
    from app.models import McpServerRegistry, McpLlmAxisScore

    if start_date >= end_date:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")

    # Snapshot at start_date: most-recent score at or before start_date
    start_stmt = (
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id == server_id)
        .where(McpLlmAxisScore.scored_at <= start_date)
        .order_by(McpLlmAxisScore.scored_at.desc())
    )
    start_rows = db.execute(start_stmt).scalars().all()

    # Snapshot at end_date: most-recent score at or before end_date
    end_stmt = (
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id == server_id)
        .where(McpLlmAxisScore.scored_at <= end_date)
        .order_by(McpLlmAxisScore.scored_at.desc())
    )
    end_rows = db.execute(end_stmt).scalars().all()

    if not start_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No scoring records found for server '{server_id}' on or before start_date",
        )
    if not end_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No scoring records found for server '{server_id}' on or before end_date",
        )

    # Build per-axis lookup for start snapshot
    start_map: dict[str, McpLlmAxisScore] = {r.axis_name: r for r in start_rows}
    end_map: dict[str, McpLlmAxisScore] = {r.axis_name: r for r in end_rows}

    all_axes = set(start_map.keys()) | set(end_map.keys())

    # Snapshot timestamps (most-recent row's scored_at)
    start_ts = start_rows[0].scored_at
    end_ts = end_rows[0].scored_at

    if start_ts == end_ts and start_rows[0].server_id == end_rows[0].server_id:
        raise HTTPException(
            status_code=404,
            detail="Start and end snapshots refer to the same scoring record; choose distinct dates",
        )

    axes: list[AxisDelta] = []
    overall_delta = 0.0

    for axis_name in sorted(all_axes):
        start_rec = start_map.get(axis_name)
        end_rec = end_map.get(axis_name)

        s_p = float(start_rec.p_top) if start_rec and start_rec.p_top is not None else None
        e_p = float(end_rec.p_top) if end_rec and end_rec.p_top is not None else None

        delta = round(e_p - s_p, 6) if s_p is not None and e_p is not None else 0.0
        overall_delta += abs(delta)

        axes.append(
            AxisDelta(
                axis_name=axis_name,
                start_label=start_rec.label if start_rec else None,
                end_label=end_rec.label if end_rec else None,
                start_p_top=s_p,
                end_p_top=e_p,
                delta=delta,
            )
        )

    # Determine risk tiers from registry
    start_tier: Optional[str] = None
    end_tier: Optional[str] = None

    tier_row = db.execute(
        select(McpServerRegistry.risk_tier).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()
    if tier_row:
        start_tier = tier_row
        end_tier = tier_row

    return ScoringDeltaCalculationResponse(
        server_id=server_id,
        start_scored_at=start_ts.isoformat() if isinstance(start_ts, datetime) else str(start_ts),
        end_scored_at=end_ts.isoformat() if isinstance(end_ts, datetime) else str(end_ts),
        start_risk_tier=start_tier,
        end_risk_tier=end_tier,
        tier_changed=(start_tier != end_tier),
        axes=axes,
        overall_delta=round(overall_delta, 6),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    _repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry, McpLlmAxisScore

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

    now = datetime.now(timezone.utc)
    d1 = now - timedelta(days=2)
    d2 = now - timedelta(days=1)

    with TestSession() as sess:
        sess.add(McpServerRegistry(server_id="srv-delta-1", name="Delta Test Server", risk_tier="LOW"))
        sess.add_all([
            # Day-2 snapshot: overall_risk HIGH, auth_strength MEDIUM
            McpLlmAxisScore(
                id=1,
                server_id="srv-delta-1", axis_name="overall_risk",
                label="HIGH", p_top=0.70,
                model_version="v1", scored_at=d1,
                adapter_sha256="a" * 64, decision_rule_version="v1",
                probs="{}", p_critical=0.2, p_danger=0.3,
                escalated=False, label_index=2,
            ),
            McpLlmAxisScore(
                id=2,
                server_id="srv-delta-1", axis_name="auth_strength",
                label="MEDIUM", p_top=0.45,
                model_version="v1", scored_at=d1,
                adapter_sha256="b" * 64, decision_rule_version="v1",
                probs="{}", p_critical=0.1, p_danger=0.2,
                escalated=False, label_index=1,
            ),
            # Day-1 snapshot: overall_risk LOW, auth_strength STRONG
            McpLlmAxisScore(
                id=3,
                server_id="srv-delta-1", axis_name="overall_risk",
                label="LOW", p_top=0.20,
                model_version="v1", scored_at=d2,
                adapter_sha256="c" * 64, decision_rule_version="v1",
                probs="{}", p_critical=0.0, p_danger=0.05,
                escalated=False, label_index=0,
            ),
            McpLlmAxisScore(
                id=4,
                server_id="srv-delta-1", axis_name="auth_strength",
                label="STRONG", p_top=0.85,
                model_version="v1", scored_at=d2,
                adapter_sha256="d" * 64, decision_rule_version="v1",
                probs="{}", p_critical=0.0, p_danger=0.05,
                escalated=False, label_index=3,
            ),
            # srv-delta-2: only one snapshot (should return 404 for delta)
            McpLlmAxisScore(
                id=5,
                server_id="srv-delta-2", axis_name="overall_risk",
                label="LOW", p_top=0.15,
                model_version="v1", scored_at=now,
                adapter_sha256="e" * 64, decision_rule_version="v1",
                probs="{}", p_critical=0.0, p_danger=0.05,
                escalated=False, label_index=0,
            ),
        ])
        sess.commit()

    client = TestClient(test_app)

    # Test 1: happy path — delta between two snapshots
    r1 = client.get(
        "/api/scoring-delta-calculation/srv-delta-1",
        params={"start_date": d1.isoformat(), "end_date": d2.isoformat()},
    )
    if r1.status_code != 200:
        print(f"FAIL: expected 200, got {r1.status_code}: {r1.text}")
        sys.exit(1)
    d = r1.json()
    if d["server_id"] != "srv-delta-1":
        print(f"FAIL: server_id mismatch: {d['server_id']}")
        sys.exit(1)
    if d["overall_delta"] <= 0.0:
        print(f"FAIL: overall_delta should be > 0, got {d['overall_delta']}")
        sys.exit(1)
    if len(d["axes"]) != 2:
        print(f"FAIL: expected 2 axes, got {len(d['axes'])}")
        sys.exit(1)
    axis_map = {a["axis_name"]: a for a in d["axes"]}
    if abs(axis_map["overall_risk"]["delta"] - (-0.50)) > 0.001:
        print(f"FAIL: overall_risk delta should be -0.50, got {axis_map['overall_risk']['delta']}")
        sys.exit(1)
    if abs(axis_map["auth_strength"]["delta"] - (0.40)) > 0.001:
        print(f"FAIL: auth_strength delta should be 0.40, got {axis_map['auth_strength']['delta']}")
        sys.exit(1)
    if axis_map["overall_risk"]["start_label"] != "HIGH":
        print(f"FAIL: overall_risk start_label should be HIGH, got {axis_map['overall_risk']['start_label']}")
        sys.exit(1)
    if axis_map["overall_risk"]["end_label"] != "LOW":
        print(f"FAIL: overall_risk end_label should be LOW, got {axis_map['overall_risk']['end_label']}")
        sys.exit(1)

    # Test 2: 400 — start_date >= end_date
    r2 = client.get(
        "/api/scoring-delta-calculation/srv-delta-1",
        params={"start_date": d2.isoformat(), "end_date": d1.isoformat()},
    )
    if r2.status_code != 400:
        print(f"FAIL: start >= end should 400, got {r2.status_code}")
        sys.exit(1)

    # Test 3: 404 — no records before start_date
    ancient = now - timedelta(days=365)
    r3 = client.get(
        "/api/scoring-delta-calculation/srv-delta-1",
        params={"start_date": ancient.isoformat(), "end_date": ancient.isoformat()},
    )
    if r3.status_code != 404:
        print(f"FAIL: ancient date should 404, got {r3.status_code}")
        sys.exit(1)

    # Test 4: 404 — nonexistent server
    r4 = client.get(
        "/api/scoring-delta-calculation/nonexistent-server",
        params={"start_date": d1.isoformat(), "end_date": d2.isoformat()},
    )
    if r4.status_code != 404:
        print(f"FAIL: nonexistent server should 404, got {r4.status_code}")
        sys.exit(1)

    # Test 5: same-date snapshot returns 404
    r5 = client.get(
        "/api/scoring-delta-calculation/srv-delta-1",
        params={"start_date": d2.isoformat(), "end_date": d2.isoformat()},
    )
    if r5.status_code != 404:
        print(f"FAIL: same date should 404, got {r5.status_code}: {r5.text}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)
