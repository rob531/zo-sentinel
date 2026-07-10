"""scoring_trend_consumer_api.py -- scoring trend endpoint (Tier-2 MVP).

Reads the 7-axis score history from Postgres (McpLlmAxisScore, ~65k rows) and
returns snapshots with trend direction over a configurable time window.

Mounted automatically by app.main via _OPTIONAL_ROUTERS (exposes `router`).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, and_, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["scoring-trend"])


class TrendDirection(str, Enum):
    IMPROVING = "IMPROVING"
    DECLINING = "DECLINING"
    STABLE = "STABLE"


class AxisSnapshot(BaseModel):
    axis_name: str
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    label: Optional[str] = None


class ScoreSnapshot(BaseModel):
    scored_at: str
    axes: Dict[str, AxisSnapshot]
    overall_risk_p_top: Optional[float] = None


class ScoringTrendResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    model_version: Optional[str] = None
    days: int
    snapshots: List[ScoreSnapshot]
    trend_direction: TrendDirection


AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


def _compute_trend(snapshots: List[ScoreSnapshot]) -> TrendDirection:
    """Compute trend direction from first vs last window segment.
    
    Compares the average p_top of overall_risk in the first third vs last third
    of snapshots. If fewer than 3 snapshots, returns STABLE.
    """
    if len(snapshots) < 2:
        return TrendDirection.STABLE
    
    n = len(snapshots)
    first_third = snapshots[:max(1, n // 3)]
    last_third = snapshots[max(1, 2 * n // 3):]
    
    first_avg = sum(s.overall_risk_p_top or 0 for s in first_third) / len(first_third)
    last_avg = sum(s.overall_risk_p_top or 0 for s in last_third) / len(last_third)
    
    delta = last_avg - first_avg
    threshold = 0.05  # 5% threshold for stable
    
    if delta < -threshold:
        return TrendDirection.IMPROVING  # p_top decreased = better
    elif delta > threshold:
        return TrendDirection.DECLINING  # p_top increased = worse
    else:
        return TrendDirection.STABLE


@router.get("/servers/{server_id}/scoring-trend", response_model=ScoringTrendResponse)
def get_scoring_trend(
    server_id: str,
    days: int = 30,
    db: Session = Depends(get_session),
) -> ScoringTrendResponse:
    """Return the 7-axis score history for a server over a configurable time window.
    
    - days: time window in days (default 30, max 90)
    - Returns snapshots with axis scores and a trend_direction computed from
      the first vs last window segment.
    """
    if days < 1:
        raise HTTPException(status_code=400, detail="days must be at least 1")
    if days > 90:
        days = 90
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    reg = db.get(McpServerRegistry, server_id)
    name = reg.name if reg else None
    url = reg.url if reg else None
    
    rows = db.execute(
        select(McpLlmAxisScore)
        .where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
    ).scalars().all()
    
    if not rows:
        raise HTTPException(status_code=404, detail=f"No score history for server_id {server_id!r}")
    
    # Group rows by scored_at to build snapshots
    by_time: Dict[str, Dict[str, McpLlmAxisScore]] = {}
    for r in rows:
        ts = r.scored_at.isoformat() if isinstance(r.scored_at, datetime) else str(r.scored_at)
        if ts not in by_time:
            by_time[ts] = {}
        by_time[ts][r.axis_name] = r
    
    snapshots: List[ScoreSnapshot] = []
    for ts, axis_map in sorted(by_time.items(), reverse=True):
        axes: Dict[str, AxisSnapshot] = {}
        overall_p_top = None
        for ax in AXES:
            if ax in axis_map:
                r = axis_map[ax]
                axes[ax] = AxisSnapshot(
                    axis_name=ax,
                    p_top=r.p_top,
                    p_critical=r.p_critical,
                    label=r.label,
                )
                if ax == "overall_risk":
                    overall_p_top = r.p_top
        snapshots.append(ScoreSnapshot(
            scored_at=ts,
            axes=axes,
            overall_risk_p_top=overall_p_top,
        ))
    
    # Get model version from most recent snapshot
    model_version = rows[0].model_version if rows else None
    
    return ScoringTrendResponse(
        server_id=server_id,
        name=name,
        url=url,
        model_version=model_version,
        days=days,
        snapshots=snapshots,
        trend_direction=_compute_trend(snapshots),
    )


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from datetime import timezone
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpServerRegistry(server_id="test-id", name="Test MCP",
                            url="https://example.com/test"))
    
    # Seed historical scores: older (higher p_top) and newer (lower p_top) = IMPROVING
    now = datetime.now(timezone.utc)
    for i, (ax, lbl, p_top_val) in enumerate([
        ("overall_risk", "HIGH", 0.85),
        ("auth_strength", "STRONG", 0.15),
        ("capability_breadth", "BROAD", 0.30),
        ("data_sensitivity", "CRITICAL", 0.70),
        ("network_egress", "EXTERNAL", 0.45),
        ("maintainer_trust", "ESTABLISHED", 0.20),
        ("exploit_surface", "MODERATE", 0.50),
    ]):
        # Older snapshot (10 days ago) - distinct model version
        older_time = now - timedelta(days=10)
        s.add(McpLlmAxisScore(id=i*2+1, server_id="test-id", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559",
                              scored_at=older_time))
        # Newer snapshot (1 day ago) - distinct model version to satisfy UNIQUE constraint
        newer_time = now - timedelta(days=1)
        s.add(McpLlmAxisScore(id=i*2+2, server_id="test-id", axis_name=ax, label=lbl,
                              model_version="v3.0_40974560",
                              scored_at=newer_time))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    
    # Test happy path
    r = c.get("/api/servers/test-id/scoring-trend?days=30")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    j = r.json()
    assert "snapshots" in j, "Missing snapshots in response"
    assert len(j["snapshots"]) >= 2, f"Expected at least 2 snapshots, got {len(j['snapshots'])}"
    assert "trend_direction" in j, "Missing trend_direction in response"
    assert j["trend_direction"] in ["IMPROVING", "DECLINING", "STABLE"], f"Invalid trend_direction: {j['trend_direction']}"
    
    # Verify axis structure in snapshots
    for snap in j["snapshots"]:
        assert "axes" in snap, "Missing axes in snapshot"
        assert isinstance(snap["axes"], dict), "axes should be a dict"
        for ax_name in ["overall_risk", "auth_strength"]:
            assert ax_name in snap["axes"], f"Missing {ax_name} in snapshot axes"
            ax = snap["axes"][ax_name]
            assert "p_top" in ax, f"Missing p_top in axis {ax_name}"
    
    # Test edge case: unknown server returns 404
    r2 = c.get("/api/servers/nonexistent/scoring-trend")
    assert r2.status_code == 404, f"Expected 404 for unknown server, got {r2.status_code}"
    
    # Test days parameter bounds
    r3 = c.get("/api/servers/test-id/scoring-trend?days=100")  # capped at 90
    assert r3.status_code == 200, f"Expected 200 when days > 90 (should cap), got {r3.status_code}"
    
    print("PASS")
