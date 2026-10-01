# deps: fastapi, sqlalchemy, pydantic
"""Scoring Run Comparison API.

Compares two scoring runs identified by ISO-8601 scored_at timestamps, returning
per-axis p_top deltas and tier transition counts across servers.

GET /api/scoring/run-comparison
  Params: run_a (ISO timestamp), run_b (ISO timestamp)
  Returns: axis_deltas, tier_transitions {upgrades, downgrades, stable}, server_deltas.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_llm_axis_scores.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Ensure repo root is on path for app imports
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

# Import the db module directly as a script module to avoid triggering
# app/__init__.py which has broken router imports.
# This works because the path manipulation above makes app/ importable.
import app.db as _app_db
import app.models as _app_models

get_session = _app_db.get_session
Base = _app_db.Base
McpLlmAxisScore = _app_models.McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["scoring_run_comparison_api"])


# --------------------------------------------------------------------------- #
# Pydantic request / response models
# --------------------------------------------------------------------------- #


class AxisDelta(BaseModel):
    axis_name: str
    delta_mean: float
    delta_max: float


class ServerDelta(BaseModel):
    server_id: str
    tier_from: str | None
    tier_to: str | None
    max_axis_delta: float


class TierTransitions(BaseModel):
    upgrades: int
    downgrades: int
    stable: int


class ComparisonResponse(BaseModel):
    run_a: str
    run_b: str
    axis_deltas: List[AxisDelta]
    tier_transitions: TierTransitions
    server_deltas: List[ServerDelta]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _fetch_scores(session: Session, scored_at: datetime.datetime) -> List[McpLlmAxisScore]:
    return (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.scored_at == scored_at)
        .all()
    )


def _compute_comparison(
    scores_a: List[McpLlmAxisScore],
    scores_b: List[McpLlmAxisScore],
) -> Tuple[List[AxisDelta], TierTransitions, List[ServerDelta]]:
    # Index scores by (server_id, axis_name)
    map_a: Dict[Tuple[str, str], McpLlmAxisScore] = {
        (s.server_id, s.axis_name): s for s in scores_a
    }
    map_b: Dict[Tuple[str, str], McpLlmAxisScore] = {
        (s.server_id, s.axis_name): s for s in scores_b
    }

    # ---------- Axis deltas ----------
    axis_to_deltas: Dict[str, List[float]] = {}
    for (srv, axis), s_a in map_a.items():
        s_b = map_b.get((srv, axis))
        if s_b is None:
            continue
        delta = (s_b.p_top or 0.0) - (s_a.p_top or 0.0)
        axis_to_deltas.setdefault(axis, []).append(delta)

    axis_deltas: List[AxisDelta] = []
    for axis, deltas in axis_to_deltas.items():
        mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
        max_delta = max(deltas) if deltas else 0.0
        axis_deltas.append(AxisDelta(axis_name=axis, delta_mean=mean_delta, delta_max=max_delta))

    # ---------- Server deltas ----------
    server_to_deltas: Dict[str, List[float]] = {}
    server_to_tiers: Dict[str, Tuple[str | None, str | None]] = {}
    for (srv, axis), s_a in map_a.items():
        s_b = map_b.get((srv, axis))
        if s_b is None:
            continue
        delta = abs((s_b.p_top or 0.0) - (s_a.p_top or 0.0))
        server_to_deltas.setdefault(srv, []).append(delta)
        if srv not in server_to_tiers:
            server_to_tiers[srv] = (s_a.label, s_b.label)

    server_deltas: List[ServerDelta] = []
    upgrades = downgrades = stable = 0
    for srv, deltas in server_to_deltas.items():
        max_delta = max(deltas) if deltas else 0.0
        tier_from, tier_to = server_to_tiers.get(srv, (None, None))
        if tier_from is not None and tier_to is not None:
            if tier_from == tier_to:
                stable += 1
            elif tier_to > tier_from:
                upgrades += 1
            else:
                downgrades += 1
        server_deltas.append(
            ServerDelta(
                server_id=srv,
                tier_from=tier_from,
                tier_to=tier_to,
                max_axis_delta=max_delta,
            )
        )

    tier_transitions = TierTransitions(
        upgrades=upgrades, downgrades=downgrades, stable=stable
    )
    return axis_deltas, tier_transitions, server_deltas


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


@router.get("/scoring/run-comparison", response_model=ComparisonResponse)
def scoring_run_comparison(
    run_a: str = Query(..., description="ISO-8601 scored_at timestamp for run A"),
    run_b: str = Query(..., description="ISO-8601 scored_at timestamp for run B"),
    session: Session = Depends(get_session),
) -> ComparisonResponse:
    """Compare two scoring runs by their scored_at timestamps."""
    try:
        ts_a = datetime.datetime.fromisoformat(run_a)
        ts_b = datetime.datetime.fromisoformat(run_b)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp format: {exc}")

    scores_a = _fetch_scores(session, ts_a)
    scores_b = _fetch_scores(session, ts_b)

    if not scores_a or not scores_b:
        raise HTTPException(status_code=404, detail="One or both runs not found")

    axis_deltas, tier_transitions, server_deltas = _compute_comparison(scores_a, scores_b)

    return ComparisonResponse(
        run_a=run_a,
        run_b=run_b,
        axis_deltas=axis_deltas,
        tier_transitions=tier_transitions,
        server_deltas=server_deltas,
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

    # In-memory SQLite engine for test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    def get_test_session() -> Session:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    app.include_router(router)

    # Seed test data: 3 servers, 2 runs
    now = datetime.datetime.utcnow()
    earlier = now - datetime.timedelta(hours=1)

    test_rows = [
        # Server 1 – upgrade on axis "security"
        McpLlmAxisScore(server_id="srv-1", axis_name="security", p_top=0.3, label="low", scored_at=earlier),
        McpLlmAxisScore(server_id="srv-1", axis_name="security", p_top=0.7, label="medium", scored_at=now),
        McpLlmAxisScore(server_id="srv-1", axis_name="reliability", p_top=0.5, label="medium", scored_at=earlier),
        McpLlmAxisScore(server_id="srv-1", axis_name="reliability", p_top=0.5, label="medium", scored_at=now),
        # Server 2 – downgrade on axis "security"
        McpLlmAxisScore(server_id="srv-2", axis_name="security", p_top=0.8, label="high", scored_at=earlier),
        McpLlmAxisScore(server_id="srv-2", axis_name="security", p_top=0.4, label="low", scored_at=now),
        McpLlmAxisScore(server_id="srv-2", axis_name="reliability", p_top=0.5, label="medium", scored_at=earlier),
        McpLlmAxisScore(server_id="srv-2", axis_name="reliability", p_top=0.5, label="medium", scored_at=now),
        # Server 3 – stable
        McpLlmAxisScore(server_id="srv-3", axis_name="security", p_top=0.5, label="medium", scored_at=earlier),
        McpLlmAxisScore(server_id="srv-3", axis_name="security", p_top=0.5, label="medium", scored_at=now),
        McpLlmAxisScore(server_id="srv-3", axis_name="reliability", p_top=0.5, label="medium", scored_at=earlier),
        McpLlmAxisScore(server_id="srv-3", axis_name="reliability", p_top=0.5, label="medium", scored_at=now),
    ]

    with TestSession() as db:
        db.add_all(test_rows)
        db.commit()

    client = TestClient(app)

    # Happy path: valid timestamps
    resp = client.get(
        "/api/scoring/run-comparison",
        params={"run_a": earlier.isoformat(), "run_b": now.isoformat()},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "axis_deltas" in data and isinstance(data["axis_deltas"], list)
    assert "tier_transitions" in data
    tt = data["tier_transitions"]
    assert tt["upgrades"] + tt["downgrades"] + tt["stable"] == 3, f"All 3 servers should be accounted for: {tt}"
    assert tt["upgrades"] >= 1, "srv-1 upgraded"
    assert tt["downgrades"] >= 1, "srv-2 downgraded"

    # 404: non-existent run
    resp_404 = client.get(
        "/api/scoring/run-comparison",
        params={
            "run_a": earlier.isoformat(),
            "run_b": (now + datetime.timedelta(days=30)).isoformat(),
        },
    )
    assert resp_404.status_code == 404, f"Expected 404 for missing run B, got {resp_404.status_code}"

    # 400: invalid timestamp
    resp_400 = client.get(
        "/api/scoring/run-comparison",
        params={"run_a": "not-a-timestamp", "run_b": now.isoformat()},
    )
    assert resp_400.status_code == 400, f"Expected 400 for bad timestamp, got {resp_400.status_code}"

    print("PASS")
