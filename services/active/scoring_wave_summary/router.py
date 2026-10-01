# deps: fastapi, pydantic, sqlalchemy
"""Scoring Wave Summary Router.

Provides GET /api/scoring_wave_summary returning wave-windowed aggregations
of McpLlmAxisScore joined with McpServerRegistry risk_tier.

Public endpoint (no auth required per service spec).
Data: app Postgres via SQLAlchemy Session (from app.db import get_session).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, distinct
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["scoring_wave_summary"])


class TierDistribution(BaseModel):
    TRUSTED_GENERAL: int = 0
    HIGH_RISK_ISOLATED: int = 0
    LOW_RISK_EXPOSED: int = 0
    UNKNOWN: int = 0


class WaveSummary(BaseModel):
    wave_id: str
    wave_start: datetime
    wave_end: datetime
    total_scores: int
    unique_servers: int
    avg_p_top: Optional[float] = None
    avg_p_critical: Optional[float] = None
    avg_p_danger: Optional[float] = None
    tier_distribution: TierDistribution
    model_version: Optional[str] = None


class ScoringWaveSummaryResponse(BaseModel):
    wave_window_hours: int
    waves: list[WaveSummary]
    total_waves: int


def _compute_wave_start(ts: datetime, wave_hours: int) -> datetime:
    """Return the start of the wave window containing ts."""
    total_minutes = ts.hour * 60 + ts.minute
    wave_slot = total_minutes // (wave_hours * 60)
    wave_start_minute = wave_slot * (wave_hours * 60)
    base = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(minutes=wave_start_minute)


@router.get("/scoring_wave_summary", response_model=ScoringWaveSummaryResponse)
def get_scoring_wave_summary(
    wave_window_hours: int = Query(default=6, ge=1, le=72),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_session),
) -> ScoringWaveSummaryResponse:
    """
    Aggregate McpLlmAxisScore rows into wave windows and return
    per-wave stats including tier distribution from McpServerRegistry.

    A wave is a contiguous time bucket of `wave_window_hours` duration.
    Servers in each wave are bucketed by their current risk_tier.
    """
    # Fetch all axis scores with server risk_tier
    scores_q = (
        select(
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.p_danger,
            McpLlmAxisScore.model_version,
            McpServerRegistry.risk_tier,
        )
        .join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(limit * 10)  # over-fetch; thin filter
    )
    rows = db.execute(scores_q).all()

    # Group into wave windows
    wave_map: dict[tuple, dict] = {}
    for row in rows:
        scored_at = row.scored_at
        if scored_at is None:
            continue
        wave_start = _compute_wave_start(scored_at, wave_window_hours)
        wave_end = wave_start + timedelta(hours=wave_window_hours)
        key = (wave_start, wave_end)

        if key not in wave_map:
            wave_map[key] = {
                "scores": [],
                "servers": set(),
                "total_p_top": 0.0,
                "total_p_critical": 0.0,
                "total_p_danger": 0.0,
                "tier_counts": {},
                "model_version": row.model_version,
            }

        w = wave_map[key]
        w["scores"].append(row)
        w["servers"].add(row.server_id)
        if row.p_top is not None:
            w["total_p_top"] += row.p_top
        if row.p_critical is not None:
            w["total_p_critical"] += row.p_critical
        if row.p_danger is not None:
            w["total_p_danger"] += row.p_danger

        tier = row.risk_tier or "UNKNOWN"
        w["tier_counts"][tier] = w["tier_counts"].get(tier, 0) + 1

    # Sort waves newest-first
    sorted_keys = sorted(wave_map.keys(), reverse=True)
    waves: list[WaveSummary] = []
    for wave_start, wave_end in sorted_keys[:limit]:
        w = wave_map[(wave_start, wave_end)]
        n = len(w["scores"])
        tier_dist = TierDistribution(
            **{
                "TRUSTED_GENERAL": w["tier_counts"].get("TRUSTED_GENERAL", 0),
                "HIGH_RISK_ISOLATED": w["tier_counts"].get("HIGH_RISK_ISOLATED", 0),
                "LOW_RISK_EXPOSED": w["tier_counts"].get("LOW_RISK_EXPOSED", 0),
                "UNKNOWN": w["tier_counts"].get("UNKNOWN", 0),
            }
        )
        waves.append(
            WaveSummary(
                wave_id=wave_start.strftime("%Y%m%d%H%M"),
                wave_start=wave_start,
                wave_end=wave_end,
                total_scores=n,
                unique_servers=len(w["servers"]),
                avg_p_top=round(w["total_p_top"] / n, 4) if n else None,
                avg_p_critical=round(w["total_p_critical"] / n, 4) if n else None,
                avg_p_danger=round(w["total_p_danger"] / n, 4) if n else None,
                tier_distribution=tier_dist,
                model_version=w["model_version"],
            )
        )

    return ScoringWaveSummaryResponse(
        wave_window_hours=wave_window_hours,
        waves=waves,
        total_waves=len(waves),
    )


if __name__ == "__main__":
    import sys

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
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)

    db = SessionLocal()
    # 2 servers
    db.add(McpServerRegistry(server_id="srv1", name="Srv 1", risk_tier="TRUSTED_GENERAL"))
    db.add(McpServerRegistry(server_id="srv2", name="Srv 2", risk_tier="HIGH_RISK_ISOLATED"))
    db.commit()

    # Wave 1: 2 scores within same 6h window
    for offset_h in (0, 2):
        db.add(
            McpLlmAxisScore(
                server_id="srv1",
                axis_name="overall_risk",
                label="LOW",
                p_top=0.9,
                p_critical=0.05,
                p_danger=0.1,
                model_version="v1",
                scored_at=base + timedelta(hours=offset_h),
            )
        )
    # Wave 2: 1 score 8h later (new wave)
    db.add(
        McpLlmAxisScore(
            server_id="srv2",
            axis_name="overall_risk",
            label="HIGH",
            p_top=0.3,
            p_critical=0.6,
            p_danger=0.8,
            model_version="v1",
            scored_at=base + timedelta(hours=8),
        )
    )
    db.commit()
    db.close()

    def _override():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)
    resp = client.get("/api/scoring_wave_summary?wave_window_hours=6&limit=10")
    assert resp.status_code == 200, f"status={resp.status_code} body={resp.text}"
    data = resp.json()
    assert "waves" in data, f"no waves key: {data}"
    waves = data["waves"]
    assert len(waves) == 2, f"Expected 2 waves, got {len(waves)}: {[w['wave_id'] for w in waves]}"

    # Wave 1 should have TRUSTED_GENERAL
    w1 = next(w for w in waves if w["total_scores"] == 2)
    assert w1["tier_distribution"]["TRUSTED_GENERAL"] == 2, f"wave1 tier dist: {w1['tier_distribution']}"

    # Wave 2 should have HIGH_RISK_ISOLATED
    w2 = next(w for w in waves if w["total_scores"] == 1)
    assert w2["tier_distribution"]["HIGH_RISK_ISOLATED"] == 1, f"wave2 tier dist: {w2['tier_distribution']}"

    print("PASS")
