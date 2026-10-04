# deps: fastapi, pydantic, sqlalchemy, requests
"""Scoring Pipeline Health API.

Provides a health/readiness overview of the entire scoring pipeline by combining:
  (1) App DB  -- McpServerRegistry + McpLlmAxisScore: axis completeness and
      scoring recency per server.
  (2) Write service -- mcp_signal_scores, mesh_memory: pipeline-level run
      frequency and cadence from the ZoComputer store.

Auth: public.  Prefix: /api.  Tag: scoring_pipeline_health.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_repo = _Path(__file__).resolve().parents[3]
if str(_repo) not in _sys.path:
    _sys.path.insert(0, str(_repo))

from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api/scoring/pipeline", tags=["scoring_pipeline_health"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"

# Scoring frequency thresholds (hours)
_MAX_HOURS_BETWEEN_RUNS = 24   # pipeline considered stale if gap > 24 h
_MIN_SCORES_PER_HOUR = 1       # minimum score writes expected per hour

# Completeness thresholds
_MIN_COMPLETENESS_PCT = 80.0


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class PipelineAxisSummary(BaseModel):
    axis_name: str
    scored_servers: int
    coverage_pct: float
    model_config = ConfigDict(from_attributes=True)


class ScoringFreshness(BaseModel):
    newest_score_at: Optional[str] = None
    oldest_untested_server: Optional[str] = None
    median_score_age_hours: float
    stale_server_count: int
    model_config = ConfigDict(from_attributes=True)


class PipelineCadence(BaseModel):
    last_run_at: Optional[str] = None
    runs_last_24h: int
    runs_last_7d: int
    avg_runs_per_day: float
    cadence_healthy: bool
    model_config = ConfigDict(from_attributes=True)


class PipelineCompleteness(BaseModel):
    total_registry_servers: int
    scored_servers: int
    fully_scored_servers: int
    completeness_pct: float
    healthy: bool
    model_config = ConfigDict(from_attributes=True)


class ScoringPipelineHealthResponse(BaseModel):
    generated_at: str
    overall_status: str  # HEALTHY | DEGRADED | UNHEALTHY
    reason: Optional[str]
    completeness: PipelineCompleteness
    freshness: ScoringFreshness
    cadence: PipelineCadence
    by_axis: list[PipelineAxisSummary]
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

ALL_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]

_STALE_HOURS = 72   # a server is stale if it has no score in 72 h


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _status_from_flags(completeness_ok: bool, freshness_ok: bool, cadence_ok: bool) -> tuple[str, str]:
    ok_count = sum([completeness_ok, freshness_ok, cadence_ok])
    if ok_count >= 3:
        return "HEALTHY", None
    if ok_count == 2:
        return "DEGRADED", "One pipeline dimension is unhealthy"
    if ok_count == 1:
        return "DEGRADED", "Two pipeline dimensions are unhealthy"
    return "UNHEALTHY", "All pipeline dimensions are unhealthy"


def _call_write_service(sql: str, params: list = None) -> list[dict]:
    payload = {"sql": sql, "params": params or []}
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query", json=payload, timeout=10
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"write_service unavailable: {exc}",
        )
    result = resp.json()
    return result.get("rows", [])


def _fetch_cadence_from_write_service() -> PipelineCadence:
    """Query the mesh/pipeline tables via write_service for run cadence."""
    now_ts = _now()
    rows_24h = 0
    rows_7d = 0
    last_run_at: Optional[datetime] = None

    # Attempt to read from mcp_signal_scores or mesh_memory
    for table in ["mcp_signal_scores", "mesh_memory"]:
        try:
            rows = _call_write_service(
                f"SELECT scored_at FROM {table} ORDER BY scored_at DESC LIMIT 1000"
            )
        except HTTPException:
            continue

        if not rows:
            continue

        cutoff_24h = (now_ts - timedelta(hours=24)).astimezone(timezone.utc).replace(tzinfo=None)
        cutoff_7d = (now_ts - timedelta(days=7)).astimezone(timezone.utc).replace(tzinfo=None)

        for row in rows:
            ts_val = row.get("scored_at") or row.get("created_at")
            if not ts_val:
                continue
            try:
                ts = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
                ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                continue

            if ts >= cutoff_7d:
                rows_7d += 1
                if ts >= cutoff_24h:
                    rows_24h += 1
                if last_run_at is None or ts > last_run_at:
                    last_run_at = ts
        break

    runs_last_24h = rows_24h
    runs_last_7d = rows_7d
    avg_per_day = round(rows_7d / 7, 2) if rows_7d else 0.0
    cadence_healthy = runs_last_24h >= _MIN_SCORES_PER_HOUR or rows_7d >= 1

    return PipelineCadence(
        last_run_at=last_run_at.isoformat() if last_run_at else None,
        runs_last_24h=runs_last_24h,
        runs_last_7d=runs_last_7d,
        avg_runs_per_day=avg_per_day,
        cadence_healthy=cadence_healthy,
    )


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/health",
    response_model=ScoringPipelineHealthResponse,
    summary="Get scoring pipeline health across registry + write-service",
)
def scoring_pipeline_health(
    db: Session = Depends(get_session),
) -> ScoringPipelineHealthResponse:
    """
    Return an aggregated health view of the scoring pipeline:

    1. **Completeness** -- fraction of registry servers with all 7 axes scored.
    2. **Freshness** -- median age of scores; count of servers with no score in 72 h.
    3. **Cadence** -- runs in last 24 h / 7 d from write_service
       (mcp_signal_scores / mesh_memory); healthy when at least 1 score written
       in the last 24 h.

    Overall status:

    - **HEALTHY** -- all three dimensions healthy.
    - **DEGRADED** -- one or two dimensions unhealthy.
    - **UNHEALTHY** -- all three dimensions unhealthy.
    """
    now_ts = _now()

    # ---- Completeness ----
    total_registry: int = db.execute(
        select(func.count(McpServerRegistry.server_id))
    ).scalar_one() or 0

    all_rows = (
        db.execute(
            select(McpLlmAxisScore).order_by(McpLlmAxisScore.server_id)
        )
        .scalars()
        .all()
    )

    server_axes: dict[str, set[str]] = {}
    for row in all_rows:
        sid = row.server_id
        if sid not in server_axes:
            server_axes[sid] = set()
        server_axes[sid].add(row.axis_name)

    scored_servers = len(server_axes)
    fully_scored = sum(
        1 for _sid, axes in server_axes.items()
        if len(axes) >= len(ALL_AXES)
    )
    completeness_pct = round((fully_scored / total_registry) * 100, 2) if total_registry else 0.0
    completeness_healthy = completeness_pct >= _MIN_COMPLETENESS_PCT

    # ---- Freshness ----
    score_ages_hours: list[float] = []
    stale_server_count = 0
    newest_score_at: Optional[datetime] = None
    oldest_untested: Optional[str] = None

    cutoff_stale = now_ts - timedelta(hours=_STALE_HOURS)

    for sid, axes in server_axes.items():
        if not axes:
            continue
        # Use the most recent scored_at across all axes for this server
        server_rows = [r for r in all_rows if r.server_id == sid]
        latest = max((r.scored_at for r in server_rows if r.scored_at), default=None)
        if latest is None:
            continue

        if newest_score_at is None or latest > newest_score_at:
            newest_score_at = latest

        age_hours = (now_ts - latest).total_seconds() / 3600
        score_ages_hours.append(age_hours)
        if latest < cutoff_stale:
            stale_server_count += 1
            if oldest_untested is None:
                oldest_untested = sid

    median_age = float(sorted(score_ages_hours)[len(score_ages_hours) // 2]) if score_ages_hours else 0.0
    freshness_healthy = stale_server_count == 0 and median_age < 24.0

    # ---- Cadence ----
    cadence: PipelineCadence
    try:
        cadence = _fetch_cadence_from_write_service()
    except HTTPException:
        # write_service unavailable -- degrade gracefully
        cadence = PipelineCadence(
            last_run_at=None,
            runs_last_24h=0,
            runs_last_7d=0,
            avg_runs_per_day=0.0,
            cadence_healthy=False,
        )

    # ---- Per-axis ----
    axis_counts: dict[str, int] = {ax: 0 for ax in ALL_AXES}
    for _sid, axes in server_axes.items():
        for ax in axes:
            if ax in axis_counts:
                axis_counts[ax] += 1

    by_axis = [
        PipelineAxisSummary(
            axis_name=ax,
            scored_servers=axis_counts[ax],
            coverage_pct=round((axis_counts[ax] / total_registry) * 100, 2) if total_registry else 0.0,
        )
        for ax in ALL_AXES
    ]

    # ---- Overall ----
    overall, reason = _status_from_flags(completeness_healthy, freshness_healthy, cadence.cadence_healthy)

    return ScoringPipelineHealthResponse(
        generated_at=now_ts.isoformat(),
        overall_status=overall,
        reason=reason,
        completeness=PipelineCompleteness(
            total_registry_servers=total_registry,
            scored_servers=scored_servers,
            fully_scored_servers=fully_scored,
            completeness_pct=completeness_pct,
            healthy=completeness_healthy,
        ),
        freshness=ScoringFreshness(
            newest_score_at=newest_score_at.isoformat() if newest_score_at else None,
            oldest_untested_server=oldest_untested,
            median_score_age_hours=round(median_age, 2),
            stale_server_count=stale_server_count,
        ),
        cadence=cadence,
        by_axis=by_axis,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from unittest.mock import patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    def _override_get_session():
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override_get_session

    now = _now()
    cutoff_fresh = now - timedelta(hours=24)
    cutoff_stale = now - timedelta(hours=73)

    with TestSessionLocal() as sess:
        for i in range(4):
            sess.add(McpServerRegistry(
                server_id=f"srv-{i:03d}",
                name=f"Server {i}",
                registry_source="test",
                risk_tier="MEDIUM",
                first_seen=now,
                last_scanned=now,
            ))

        # srv-000: fully complete (all 7 axes), fresh
        for idx, ax in enumerate(ALL_AXES):
            sess.add(McpLlmAxisScore(
                id=idx + 1,
                server_id="srv-000",
                axis_name=ax,
                model_version="v1",
                label="medium",
                label_index=2,
                p_top=0.5,
                p_critical=0.1,
                p_danger=0.2,
                probs={},
                scored_at=now,
            ))

        # srv-001: partial (2 axes), fresh
        for idx, ax in enumerate(["overall_risk", "auth_strength"]):
            sess.add(McpLlmAxisScore(
                id=len(ALL_AXES) + 1 + idx,
                server_id="srv-001",
                axis_name=ax,
                model_version="v1",
                label="medium",
                label_index=2,
                p_top=0.5,
                p_critical=0.1,
                p_danger=0.2,
                probs={},
                scored_at=now,
            ))

        # srv-002: stale score (>72h)
        for idx, ax in enumerate(["overall_risk"]):
            sess.add(McpLlmAxisScore(
                id=200 + idx,
                server_id="srv-002",
                axis_name=ax,
                model_version="v1",
                label="medium",
                label_index=2,
                p_top=0.5,
                p_critical=0.1,
                p_danger=0.2,
                probs={},
                scored_at=cutoff_stale,
            ))

        # srv-003: unscored (no axis rows)
        sess.commit()

    client = TestClient(test_app)

    # Patch write_service to return a healthy cadence
    fake_rows = [
        {"scored_at": (now - timedelta(minutes=30)).isoformat()},
        {"scored_at": (now - timedelta(hours=2)).isoformat()},
        {"scored_at": (now - timedelta(hours=5)).isoformat()},
    ]

    with patch("requests.post") as mock_post:
        mock_resp = type("MockResp", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"rows": fake_rows},
        })()
        mock_post.return_value = mock_resp

        resp = client.get("/api/scoring/pipeline/health")

    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}")
        sys.exit(1)

    data = resp.json()
    expected_keys = {
        "generated_at", "overall_status", "reason",
        "completeness", "freshness", "cadence", "by_axis",
    }
    if not expected_keys.issubset(data.keys()):
        print(f"FAIL: missing keys. Got {set(data.keys())}, expected {expected_keys}")
        sys.exit(1)

    # Completeness checks
    c = data["completeness"]
    if c["total_registry_servers"] != 4:
        print(f"FAIL: total_registry_servers expected 4, got {c['total_registry_servers']}")
        sys.exit(1)
    if c["scored_servers"] != 3:
        print(f"FAIL: scored_servers expected 3, got {c['scored_servers']}")
        sys.exit(1)
    if c["fully_scored_servers"] != 1:
        print(f"FAIL: fully_scored_servers expected 1, got {c['fully_scored_servers']}")
        sys.exit(1)
    if c["completeness_pct"] != 25.0:
        print(f"FAIL: completeness_pct expected 25.0, got {c['completeness_pct']}")
        sys.exit(1)

    # Freshness checks
    f = data["freshness"]
    if f["stale_server_count"] != 1:
        print(f"FAIL: stale_server_count expected 1, got {f['stale_server_count']}")
        sys.exit(1)
    if f["oldest_untested_server"] != "srv-002":
        print(f"FAIL: oldest_untested_server expected srv-002, got {f['oldest_untested_server']}")
        sys.exit(1)

    # Cadence checks (from fake write_service)
    cd = data["cadence"]
    if cd["runs_last_24h"] != 3:
        print(f"FAIL: runs_last_24h expected 3, got {cd['runs_last_24h']}")
        sys.exit(1)
    if cd["cadence_healthy"] is not True:
        print(f"FAIL: cadence_healthy expected True, got {cd['cadence_healthy']}")
        sys.exit(1)

    # By-axis checks
    if len(data["by_axis"]) != 7:
        print(f"FAIL: by_axis should have 7 entries, got {len(data['by_axis'])}")
        sys.exit(1)

    by_axis_map = {e["axis_name"]: e for e in data["by_axis"]}
    if by_axis_map["overall_risk"]["scored_servers"] != 3:
        print(f"FAIL: overall_risk scored_servers expected 3, got {by_axis_map['overall_risk']['scored_servers']}")
        sys.exit(1)
    if by_axis_map["exploit_surface"]["scored_servers"] != 1:
        print(f"FAIL: exploit_surface scored_servers expected 1, got {by_axis_map['exploit_surface']['scored_servers']}")
        sys.exit(1)

    # Overall status: completeness 25% < 80% (unhealthy), freshness 1 stale (unhealthy),
    # cadence healthy → 1/3 healthy → DEGRADED
    if data["overall_status"] not in ("DEGRADED", "UNHEALTHY"):
        print(f"FAIL: overall_status expected DEGRADED or UNHEALTHY, got {data['overall_status']}")
        sys.exit(1)

    # Verify write_service fallback when it is unreachable
    with patch("requests.post", side_effect=requests.RequestException("boom")):
        resp2 = client.get("/api/scoring/pipeline/health")
    if resp2.status_code != 200:
        print(f"FAIL: expected 200 even with write_service down, got {resp2.status_code}")
        sys.exit(1)
    data2 = resp2.json()
    if data2["cadence"]["cadence_healthy"] is not False:
        print(f"FAIL: cadence_healthy should be False when write_service unavailable, got {data2['cadence']}")
        sys.exit(1)
    if data2["overall_status"] not in ("DEGRADED", "UNHEALTHY"):
        print(f"FAIL: overall should be DEGRADED/UNHEALTHY when write_service down, got {data2['overall_status']}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)
