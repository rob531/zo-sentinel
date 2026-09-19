# deps: fastapi, pydantic, sqlalchemy
"""Scoring Wave Progress API.

GET /api/scoring/wave-progress
  Returns the current scoring wave progress:
  - total servers in registry
  - never_scanned: servers with no score record at all
  - pending_score: servers with a score record but scored_at is NULL
  - scored: servers with a scored_at timestamp
  - by_risk_tier: breakdown of scored servers by risk_tier
  - wave_count_7d: number of CadenceJobRun rows in the last 7 days

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on
  mcp_server_registry / mcp_llm_axis_scores / cadence_job_runs.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in _sys.path:
    _sys.path.insert(0, str(_repo_root))

from datetime import datetime, timedelta, timezone
from typing import Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun, McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["scoring_wave_progress"])


class WaveProgressResponse(BaseModel):
    total_servers: int = Field(..., description="Total servers in the registry")
    never_scanned: int = Field(..., description="Servers with no score record at all")
    pending_score: int = Field(..., description="Servers with a score row but no scored_at")
    scored: int = Field(..., description="Servers with a scored_at timestamp")
    by_risk_tier: Dict[str, int] = Field(
        ..., description="Count of scored servers per risk_tier"
    )
    wave_count_7d: int = Field(
        ..., description="CadenceJobRun rows in the last 7 days"
    )


@router.get("/scoring/wave-progress", response_model=WaveProgressResponse)
def get_wave_progress(db: Session = Depends(get_session)) -> WaveProgressResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    total = db.execute(
        select(func.count(McpServerRegistry.server_id))
    ).scalar_one() or 0

    # servers that have at least one score row (any axis)
    scored_subq = select(McpLlmAxisScore.server_id).distinct().subquery()

    # never_scanned: in registry but not in scored subquery
    never_scanned = db.execute(
        select(func.count(McpServerRegistry.server_id)).where(
            ~McpServerRegistry.server_id.in_(scored_subq)
        )
    ).scalar_one() or 0

    # pending: has a score row but scored_at is null
    pending_rows = (
        select(McpLlmAxisScore.server_id)
        .where(McpLlmAxisScore.scored_at.is_(None))
        .distinct()
        .subquery()
    )
    pending_score = db.execute(
        select(func.count(McpServerRegistry.server_id)).where(
            McpServerRegistry.server_id.in_(pending_rows)
        )
    ).scalar_one() or 0

    # scored: has at least one score row with scored_at not null
    scored = db.execute(
        select(func.count(McpServerRegistry.server_id)).where(
            McpServerRegistry.server_id.in_(scored_subq)
        )
    ).scalar_one() or 0

    # by_risk_tier for scored servers
    tier_rows = (
        db.execute(
            select(McpServerRegistry.risk_tier, func.count(McpServerRegistry.server_id))
            .join(McpLlmAxisScore, McpServerRegistry.server_id == McpLlmAxisScore.server_id)
            .where(McpLlmAxisScore.scored_at.isnot(None))
            .group_by(McpServerRegistry.risk_tier)
        )
        .all()
    )
    by_risk_tier: Dict[str, int] = {tier: cnt for tier, cnt in tier_rows}

    # wave_count_7d from cadence_job_runs
    wave_count_7d = (
        db.execute(
            select(func.count(CadenceJobRun.id)).where(
                CadenceJobRun.started_at >= cutoff
            )
        ).scalar_one()
        or 0
    )

    return WaveProgressResponse(
        total_servers=total,
        never_scanned=never_scanned,
        pending_score=pending_score,
        scored=scored,
        by_risk_tier=by_risk_tier,
        wave_count_7d=wave_count_7d,
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

    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        # 5 never-scanned servers
        for i in range(5):
            db.add(
                McpServerRegistry(
                    server_id=f"ns-{i}",
                    name=f"NeverScanned{i}",
                    last_scanned=None,
                    risk_tier="low",
                )
            )
        # 5 scored servers split across two risk tiers
        for i in range(5, 10):
            sid = f"sc-{i}"
            tier = "high" if i % 2 == 0 else "medium"
            db.add(
                McpServerRegistry(
                    server_id=sid,
                    name=f"Scored{i}",
                    last_scanned=now,
                    risk_tier=tier,
                )
            )
            db.add(
                McpLlmAxisScore(
                    id=i,
                    server_id=sid,
                    axis_name="overall_risk",
                    label="test",
                    label_index=0,
                    model_version="v1",
                    p_critical=0.0,
                    p_danger=0.0,
                    p_top=0.0,
                    probs={},
                    scored_at=now,
                )
            )
        # 2 cadence job runs within last 7 days
        for i in range(2):
            db.add(
                CadenceJobRun(
                    id=i,
                    job="wave",
                    detail="test",
                    status="completed",
                    started_at=now - timedelta(days=1),
                    finished_at=now,
                    rows_affected=10,
                )
            )
        db.commit()

    client = TestClient(app)
    resp = client.get("/api/scoring/wave-progress")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_servers"] == 10, f"total_servers={data['total_servers']}"
    assert data["never_scanned"] == 5, f"never_scanned={data['never_scanned']}"
    assert data["scored"] == 5, f"scored={data['scored']}"
    assert "by_risk_tier" in data
    assert data["wave_count_7d"] == 2, f"wave_count_7d={data['wave_count_7d']}"
    print("PASS")
