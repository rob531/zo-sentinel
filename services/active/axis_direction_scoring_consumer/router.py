# deps: fastapi, pydantic, sqlalchemy, requests
"""Axis Direction Scoring Consumer.

Compares current axis scores against a rolling baseline window to classify
each axis as improving / stable / declining. Results are written to the
mcp_signal_scores mesh table via write_service.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + McpLlmAxisScore ORM.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["axis_direction_scoring_consumer"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class AxisDirectionResult(BaseModel):
    axis_name: str
    direction: Literal["improving", "stable", "declining"]
    delta_p_top: float
    baseline: float
    current: float

    model_config = ConfigDict(from_attributes=True)


class ServerAxisDirectionsResponse(BaseModel):
    server_id: str
    axes: list[AxisDirectionResult]


class SignalWriteRecord(BaseModel):
    server_id: str
    signal_type: str
    signal_value: float
    metadata_json: dict[str, Any]


# --------------------------------------------------------------------------- #
# Core computation (pure function, no I/O)
# --------------------------------------------------------------------------- #

def compute_axis_directions(
    server_id: str,
    current_scores: list[dict[str, Any]],
    baseline_scores: list[dict[str, Any]],
    threshold: float = 0.05,
) -> list[AxisDirectionResult]:
    """
    Classify each axis as improving / stable / declining by comparing the most
    recent p_top against the baseline window average.

    Args:
        server_id:        used only for logging / error context
        current_scores:   [{axis_name, p_top}] -- one row per axis (most recent)
        baseline_scores:  [{axis_name, p_top}] -- avg p_top over the baseline window
        threshold:        minimum |delta| to be classified as improving/declining

    Returns:
        List of AxisDirectionResult, one per axis in current_scores.
    """
    baseline_map = {s["axis_name"]: s["p_top"] for s in baseline_scores}
    results = []

    for current in current_scores:
        axis_name = current["axis_name"]
        current_p_top = current["p_top"]
        baseline_p_top = baseline_map.get(axis_name, current_p_top)

        delta = current_p_top - baseline_p_top

        if delta > threshold:
            direction = "improving"
        elif delta < -threshold:
            direction = "declining"
        else:
            direction = "stable"

        results.append(
            AxisDirectionResult(
                axis_name=axis_name,
                direction=direction,
                delta_p_top=round(delta, 6),
                baseline=round(baseline_p_top, 6),
                current=round(current_p_top, 6),
            )
        )

    return results


# --------------------------------------------------------------------------- #
# Database helpers (app Postgres via SQLAlchemy ORM)
# --------------------------------------------------------------------------- #

def get_current_scores(
    session: Session,
    server_id: str,
    days: int = 1,
) -> list[dict[str, Any]]:
    """
    Return the most recent axis score (by scored_at DESC) per axis_name
    for the given server within the last `days` days.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    subq = (
        select(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )

    q = (
        select(McpLlmAxisScore)
        .join(
            subq,
            and_(
                McpLlmAxisScore.axis_name == subq.c.axis_name,
                McpLlmAxisScore.scored_at == subq.c.max_scored_at,
                McpLlmAxisScore.server_id == server_id,
            ),
        )
    )

    return [
        {"axis_name": row.axis_name, "p_top": row.p_top or 0.0}
        for row in session.execute(q).scalars().all()
    ]


def get_baseline_scores(
    session: Session,
    server_id: str,
    baseline_days: int = 7,
) -> list[dict[str, Any]]:
    """
    Return the average p_top per axis over the rolling baseline window.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=baseline_days)

    q = (
        select(
            McpLlmAxisScore.axis_name,
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
        )
        .where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .group_by(McpLlmAxisScore.axis_name)
    )

    return [
        {"axis_name": row.axis_name, "p_top": row.avg_p_top or 0.0}
        for row in session.execute(q).scalars().all()
    ]


# --------------------------------------------------------------------------- #
# Mesh write (via write_service HTTP -- not direct DuckDB)
# --------------------------------------------------------------------------- #

def write_signal_scores(
    server_id: str,
    directions: list[AxisDirectionResult],
) -> bool:
    """
    POST axis direction signals to write_service :8772 /query endpoint
    which owns the mcp_signal_scores mesh table.

    Non-fatal: failures are logged but do not fail the HTTP response.
    """
    import requests as _requests

    url = "http://127.0.0.1:8772/query"

    for direction in directions:
        payload = {
            "server_id": server_id,
            "signal_type": "axis_direction",
            "signal_value": (
                1.0 if direction.direction == "improving"
                else 0.5 if direction.direction == "stable"
                else 0.0
            ),
            "metadata_json": {
                "axis_name": direction.axis_name,
                "direction": direction.direction,
                "delta_p_top": direction.delta_p_top,
                "baseline": direction.baseline,
                "current": direction.current,
            },
        }
        try:
            resp = _requests.post(
                url,
                json={"sql": "INSERT INTO mcp_signal_scores (server_id, signal_type, signal_value, metadata_json) VALUES (:server_id, :signal_type, :signal_value, :metadata_json)", "params": payload},
                timeout=10,
            )
            if resp.status_code >= 500:
                # transient server error -- continue without failing
                pass
        except _requests.RequestException:
            # network error -- skip this write
            pass

    return True


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/scoring/axis-direction/{server_id}",
    response_model=ServerAxisDirectionsResponse,
)
def get_axis_direction(
    server_id: str,
    days: int = Query(default=7, ge=1, le=90, description="Baseline window in days"),
    db: Session = Depends(get_session),
) -> ServerAxisDirectionsResponse:
    """
    Compute axis direction (improving / stable / declining) for a server.

    - current  = most recent p_top per axis within the last day
    - baseline = average p_top over the rolling `days` window
    - delta > +0.05  → improving
    - delta < -0.05  → declining
    - otherwise       → stable

    Results are written to mcp_signal_scores in the background.
    """
    current_scores = get_current_scores(db, server_id, days=1)
    baseline_scores = get_baseline_scores(db, server_id, baseline_days=days)

    if not current_scores:
        raise HTTPException(
            status_code=404,
            detail=f"No axis scores found for server_id={server_id} in the last 24 hours.",
        )

    directions = compute_axis_directions(server_id, current_scores, baseline_scores)

    # Fire-and-forget write to mesh table (non-blocking for response)
    write_signal_scores(server_id, directions)

    return ServerAxisDirectionsResponse(server_id=server_id, axes=directions)


@router.post(
    "/scoring/axis-direction/{server_id}/consume",
    response_model=ServerAxisDirectionsResponse,
)
def consume_server(
    server_id: str,
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_session),
) -> ServerAxisDirectionsResponse:
    """
    Internal consumption endpoint -- same logic as GET but explicitly
    returns the result for batch callers / job runners.
    """
    current_scores = get_current_scores(db, server_id, days=1)
    baseline_scores = get_baseline_scores(db, server_id, baseline_days=days)

    if not current_scores:
        raise HTTPException(
            status_code=404,
            detail=f"No axis scores found for server_id={server_id} in the last 24 hours.",
        )

    directions = compute_axis_directions(server_id, current_scores, baseline_scores)
    write_signal_scores(server_id, directions)

    return ServerAxisDirectionsResponse(server_id=server_id, axes=directions)


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

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base as AppBase
    AppBase.metadata.create_all(test_engine)

    TestSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False
    )

    test_app = FastAPI()
    test_app.include_router(router)

    def _override_get_session():
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    test_app.dependency_overrides[get_session] = _override_get_session

    # Seed data
    base_time = datetime.now(timezone.utc)
    sess = TestSessionLocal()

    # server_a:
    #   quality  - improving:  week_ago=0.6, week_ago2=0.5, today=0.95  → baseline ~0.55, delta +0.40
    #   reliability - declining: week_ago=0.5, today=0.3 → baseline 0.5, delta -0.20
    # server_b:
    #   speed - stable: week_ago=0.7, week_ago2=0.7, today=0.71 → baseline ~0.70, delta ~0.01

    sess.add_all([
        McpLlmAxisScore(
            id=1, server_id="server_a", axis_name="quality",
            p_top=0.6, p_critical=0.2, p_danger=0.2, label="ok",
            label_index=1, model_version="v1", decision_rule_version="r1",
            scored_at=base_time - timedelta(days=6),
        ),
        McpLlmAxisScore(
            id=2, server_id="server_a", axis_name="quality",
            p_top=0.5, p_critical=0.25, p_danger=0.25, label="ok",
            label_index=1, model_version="v1", decision_rule_version="r1",
            scored_at=base_time - timedelta(days=5),
        ),
        McpLlmAxisScore(
            id=3, server_id="server_a", axis_name="quality",
            p_top=0.95, p_critical=0.03, p_danger=0.02, label="excellent",
            label_index=0, model_version="v1", decision_rule_version="r1",
            scored_at=base_time,
        ),
        McpLlmAxisScore(
            id=4, server_id="server_a", axis_name="reliability",
            p_top=0.5, p_critical=0.25, p_danger=0.25, label="medium",
            label_index=1, model_version="v1", decision_rule_version="r1",
            scored_at=base_time - timedelta(days=6),
        ),
        McpLlmAxisScore(
            id=5, server_id="server_a", axis_name="reliability",
            p_top=0.3, p_critical=0.35, p_danger=0.35, label="poor",
            label_index=2, model_version="v1", decision_rule_version="r1",
            scored_at=base_time,
        ),
        # server_b
        McpLlmAxisScore(
            id=6, server_id="server_b", axis_name="speed",
            p_top=0.7, p_critical=0.15, p_danger=0.15, label="fast",
            label_index=0, model_version="v1", decision_rule_version="r1",
            scored_at=base_time - timedelta(days=6),
        ),
        McpLlmAxisScore(
            id=7, server_id="server_b", axis_name="speed",
            p_top=0.7, p_critical=0.15, p_danger=0.15, label="fast",
            label_index=0, model_version="v1", decision_rule_version="r1",
            scored_at=base_time - timedelta(days=3),
        ),
        McpLlmAxisScore(
            id=8, server_id="server_b", axis_name="speed",
            p_top=0.71, p_critical=0.14, p_danger=0.15, label="fast",
            label_index=0, model_version="v1", decision_rule_version="r1",
            scored_at=base_time,
        ),
    ])
    sess.commit()
    sess.close()

    client = TestClient(test_app)

    # --- Test 1: server_a quality should be improving ---
    resp_a = client.get(f"/api/scoring/axis-direction/server_a?days=7")
    if resp_a.status_code != 200:
        print(f"FAIL: server_a returned {resp_a.status_code}: {resp_a.text}")
        _sys.exit(1)
    data_a = resp_a.json()
    if data_a["server_id"] != "server_a":
        print(f"FAIL: wrong server_id: {data_a['server_id']}")
        _sys.exit(1)

    axes_a = {a["axis_name"]: a for a in data_a["axes"]}
    q_dir = axes_a.get("quality", {}).get("direction")
    r_dir = axes_a.get("reliability", {}).get("direction")
    if q_dir != "improving":
        print(f"FAIL: quality direction = {q_dir}, expected improving")
        _sys.exit(1)
    if r_dir != "declining":
        print(f"FAIL: reliability direction = {r_dir}, expected declining")
        _sys.exit(1)

    # --- Test 2: server_b speed should be stable ---
    resp_b = client.get(f"/api/scoring/axis-direction/server_b?days=7")
    if resp_b.status_code != 200:
        print(f"FAIL: server_b returned {resp_b.status_code}: {resp_b.text}")
        _sys.exit(1)
    data_b = resp_b.json()
    axes_b = {a["axis_name"]: a for a in data_b["axes"]}
    s_dir = axes_b.get("speed", {}).get("direction")
    if s_dir != "stable":
        print(f"FAIL: speed direction = {s_dir}, expected stable")
        _sys.exit(1)

    # --- Test 3: 404 on unknown server ---
    resp_unknown = client.get(f"/api/scoring/axis-direction/unknown-server?days=7")
    if resp_unknown.status_code != 404:
        print(f"FAIL: expected 404 for unknown server, got {resp_unknown.status_code}")
        _sys.exit(1)

    # --- Test 4: consume endpoint returns same data ---
    resp_consume = client.post(f"/api/scoring/axis-direction/server_a/consume?days=7")
    if resp_consume.status_code != 200:
        print(f"FAIL: consume returned {resp_consume.status_code}: {resp_consume.text}")
        _sys.exit(1)
    consume_data = resp_consume.json()
    if consume_data["server_id"] != "server_a":
        print(f"FAIL: consume wrong server_id")
        _sys.exit(1)

    # --- Test 5: Pydantic round-trip (response model validates) ---
    axes = consume_data["axes"]
    for a in axes:
        if a["direction"] not in ("improving", "stable", "declining"):
            print(f"FAIL: invalid direction value: {a['direction']}")
            _sys.exit(1)

    print("PASS")
