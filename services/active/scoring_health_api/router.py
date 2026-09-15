# deps: fastapi, pydantic, sqlalchemy
"""Scoring Health API – reports scoring substrate and cadence pipeline health.

GET /api/scoring/health
  Returns scoring health (servers_scored, score_count, last_score_at, avg_age_seconds)
  and pipeline health (recent_jobs, healthy_ratio) from app-db tables.

Auth: public.
Data: app-db via get_session + SQLAlchemy ORM on mcp_llm_axis_scores / cadence_job_runs.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Generator, Optional

# Resolve 'app' from repo root BEFORE any app.* imports so __main__ works in isolation
_repo_root = Path(__file__).resolve().parents[3]  # services/active/scoring_health_api -> zo_sentinel
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, CadenceJobRun

router = APIRouter(prefix="/api", tags=["scoring_health_api"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class ScoringHealth(BaseModel):
    servers_scored: int = Field(..., description="Unique servers with at least one score")
    last_score_at: Optional[datetime] = Field(None, description="Most recent scored_at")
    score_count: int = Field(..., description="Total axis-score rows")
    avg_age_seconds: Optional[float] = Field(None, description="Avg age of scores in seconds")


class PipelineHealth(BaseModel):
    recent_jobs: int = Field(..., description="Job runs examined (last 20 by started_at)")
    healthy_ratio: float = Field(..., ge=0.0, le=1.0, description="Fraction status=success")


class ScoringHealthResponse(BaseModel):
    scoring: ScoringHealth = Field(..., description="Scoring substrate health")
    pipeline: PipelineHealth = Field(..., description="Cadence pipeline health")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _scoring_health(db: Session) -> ScoringHealth:
    result = db.execute(
        select(
            func.count(McpLlmAxisScore.id).label("score_count"),
            func.max(McpLlmAxisScore.scored_at).label("last_score_at"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("servers_scored"),
        )
    ).first()

    if result is None:
        score_count = 0
        servers_scored = 0
        last_score_at = None
    else:
        score_count = result[0] or 0
        servers_scored = result[2] or 0  # index 2 = servers_scored (count(distinct...))
        last_score_at = result[1]

    avg_age_seconds: Optional[float] = None
    if last_score_at:
        age = datetime.utcnow() - last_score_at
        avg_age_seconds = age.total_seconds()

    return ScoringHealth(
        servers_scored=servers_scored,
        last_score_at=last_score_at,
        score_count=score_count,
        avg_age_seconds=avg_age_seconds,
    )


def _pipeline_health(db: Session) -> PipelineHealth:
    rows = (
        db.query(CadenceJobRun)
        .order_by(CadenceJobRun.started_at.desc())
        .limit(20)
        .all()
    )
    recent_jobs = len(rows)
    if recent_jobs == 0:
        healthy_ratio = 1.0
    else:
        healthy_count = sum(1 for r in rows if r.status == "success")
        healthy_ratio = healthy_count / recent_jobs
    return PipelineHealth(recent_jobs=recent_jobs, healthy_ratio=round(healthy_ratio, 4))


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/scoring/health",
    response_model=ScoringHealthResponse,
    summary="Get scoring pipeline health",
)
def get_scoring_health(
    db: Session = Depends(get_session),
) -> ScoringHealthResponse:
    """
    Return health metrics for the scoring substrate (mcp_llm_axis_scores)
    and the cadence job-run pipeline (cadence_job_runs).
    Returns 200 with empty counts when no data is present.
    """
    scoring = _scoring_health(db)
    pipeline = _pipeline_health(db)
    return ScoringHealthResponse(scoring=scoring, pipeline=pipeline)


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

    from app.db import Base as AppBase
    from app.models import McpLlmAxisScore, CadenceJobRun
    from app.db import get_session as app_get_session

    # In-memory SQLite seeded with the real ORM models
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AppBase.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def _override() -> Generator[Session, None, None]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    # Seed test data
    now = datetime.utcnow()
    with TestSession() as sess:
        # 5 axis scores with distinct (server_id, axis_name, model_version) to respect UNIQUE constraint
        axis_names = ["overall_risk", "auth_strength", "capability_breadth", "data_sensitivity", "network_egress"]
        for i in range(5):
            sess.add(
                McpLlmAxisScore(
                    id=i + 1,
                    server_id=f"srv_{i}",
                    axis_name=axis_names[i],
                    label="low",
                    label_index=0,
                    probs={},
                    model_version="v1",
                    decision_rule_version="r1",
                    adapter_sha256="deadbeef",
                    p_critical=0.1,
                    p_danger=0.1,
                    p_top=0.8,
                    escalated=False,
                    escalated_to=None,
                    scored_at=now - timedelta(minutes=i * 10),
                )
            )
        # 10 cadence jobs: 8 success, 2 failed
        for i in range(10):
            sess.add(
                CadenceJobRun(
                    id=i + 1,
                    job=f"job_{i}",
                    status="success" if i < 8 else "failed",
                    started_at=now - timedelta(minutes=i),
                    finished_at=now - timedelta(minutes=i - 1),
                    rows_affected=0,
                    detail="self-test",
                )
            )
        sess.commit()

    # Build FastAPI app and wire router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[app_get_session] = _override

    client = TestClient(app)

    # Happy-path
    resp = client.get("/api/scoring/health")
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}")
        _sys.exit(1)

    data = resp.json()
    assert data["scoring"]["servers_scored"] > 0, "servers_scored must be > 0"
    assert isinstance(data["pipeline"]["healthy_ratio"], float), "healthy_ratio must be float"
    assert 0.0 <= data["pipeline"]["healthy_ratio"] <= 1.0, "healthy_ratio out of range"
    assert data["pipeline"]["healthy_ratio"] == 0.8, f"expected 0.8, got {data['pipeline']['healthy_ratio']}"
    assert data["scoring"]["score_count"] == 5, f"expected 5, got {data['scoring']['score_count']}"
    assert data["scoring"]["servers_scored"] == 5, f"expected 5, got {data['scoring']['servers_scored']}"

    print("PASS")
