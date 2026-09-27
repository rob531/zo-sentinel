# deps: fastapi, pydantic, sqlalchemy
"""Scoring Wave Summary API.

Returns job-wave summaries: CadenceJobRun rows grouped by time window with
McpLlmAxisScore aggregates per wave.

Public endpoint (auth=public).  Reads from app Postgres via SQLAlchemy Session.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["scoring_wave_summary_api"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class WaveItem(BaseModel):
    job: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    duration_seconds: int
    status: Optional[str]
    rows_affected: Optional[int]
    server_count: int
    axis_count: int
    model_versions: list[str]


class OverallStats(BaseModel):
    total_servers_scored: int
    total_axis_records: int
    unique_models: int
    first_score_at: Optional[datetime]
    last_score_at: Optional[datetime]


class WaveSummaryResponse(BaseModel):
    waves: list[WaveItem]
    overall_stats: OverallStats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCORING_KEYWORDS = ("score", "llm", "axis", "verdict")


def _is_scoring_job(job_name: str) -> bool:
    lower = job_name.lower()
    return any(kw in lower for kw in SCORING_KEYWORDS)


def _compute_wave_start(ts: datetime, wave_hours: int) -> datetime:
    """Return the start of the wave window containing ts."""
    total_minutes = ts.hour * 60 + ts.minute
    wave_slot = total_minutes // (wave_hours * 60)
    wave_start_minute = wave_slot * (wave_hours * 60)
    base = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(minutes=wave_start_minute)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/scoring_wave_summary", response_model=WaveSummaryResponse)
def get_scoring_wave_summary(
    wave_window_hours: int = Query(default=6, ge=1, le=168),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_session),
) -> WaveSummaryResponse:
    """
    Aggregate scoring jobs (CadenceJobRun) into time windows and enrich each
    window with McpLlmAxisScore aggregates (server count, axis count, model
    versions).  Returns the latest *limit* waves ordered newest-first.
    """
    # 1. Fetch scoring job runs, newest first.
    keyword_filter = or_(
        func.lower(CadenceJobRun.job).contains(kw) for kw in SCORING_KEYWORDS
    )
    job_runs = (
        db.query(CadenceJobRun)
        .filter(keyword_filter)
        .order_by(CadenceJobRun.started_at.desc())
        .limit(limit)
        .all()
    )

    # 2. For each job, fetch axis scores that fell within its window.
    wave_items: list[WaveItem] = []
    all_server_ids: set[str] = set()
    all_axis_records: list[McpLlmAxisScore] = []
    all_model_versions: set[str] = set()
    first_score: Optional[datetime] = None
    last_score: Optional[datetime] = None

    for job in job_runs:
        duration = 0
        if job.started_at and job.finished_at:
            delta = job.finished_at - job.started_at
            duration = max(0, int(delta.total_seconds()))

        # Build axis score query scoped to this job's window.
        axis_q = db.query(McpLlmAxisScore)
        if job.started_at:
            axis_q = axis_q.filter(McpLlmAxisScore.scored_at >= job.started_at)
        if job.finished_at:
            axis_q = axis_q.filter(McpLlmAxisScore.scored_at <= job.finished_at)

        wave_scores = axis_q.all()

        servers_in_wave: set[str] = set()
        model_versions_in_wave: set[str] = set()

        for s in wave_scores:
            servers_in_wave.add(s.server_id)
            all_server_ids.add(s.server_id)
            all_axis_records.append(s)
            if s.model_version:
                all_model_versions.add(s.model_version)
                model_versions_in_wave.add(s.model_version)
            if s.scored_at:
                if first_score is None or s.scored_at < first_score:
                    first_score = s.scored_at
                if last_score is None or s.scored_at > last_score:
                    last_score = s.scored_at

        wave_items.append(WaveItem(
            job=job.job,
            started_at=job.started_at,
            finished_at=job.finished_at,
            duration_seconds=duration,
            status=job.status,
            rows_affected=job.rows_affected,
            server_count=len(servers_in_wave),
            axis_count=len(wave_scores),
            model_versions=sorted(model_versions_in_wave),
        ))

    overall_stats = OverallStats(
        total_servers_scored=len(all_server_ids),
        total_axis_records=len(all_axis_records),
        unique_models=len(all_model_versions),
        first_score_at=first_score,
        last_score_at=last_score,
    )

    return WaveSummaryResponse(waves=wave_items, overall_stats=overall_stats)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

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

    # ---- Seed test data ----
    db = SessionLocal()
    t0 = datetime(2024, 1, 15, 9, 0)
    t1 = datetime(2024, 1, 15, 9, 30)
    t2 = datetime(2024, 1, 15, 10, 0)
    t3 = datetime(2024, 1, 15, 10, 45)
    t4 = datetime(2024, 1, 15, 11, 0)
    t5 = datetime(2024, 1, 15, 11, 15)

    db.add(CadenceJobRun(
        id=1, job="run_score_aggregation", status="completed",
        started_at=t0, finished_at=t1, rows_affected=5,
    ))
    db.add(CadenceJobRun(
        id=2, job="llm_verdict_processor", status="completed",
        started_at=t2, finished_at=t3, rows_affected=3,
    ))
    # Non-scoring job — should be excluded
    db.add(CadenceJobRun(
        id=3, job="backup_cleanup", status="completed",
        started_at=t4, finished_at=t5, rows_affected=0,
    ))

    # Axis scores spread across the two scoring windows.
    for i in range(1, 6):
        m = i % 3
        db.add(McpLlmAxisScore(
            id=i,
            server_id=f"s{i}",
            axis_name=f"axis_{m}",
            label="MEDIUM",
            label_index=1,
            p_top=0.6,
            p_critical=0.2,
            p_danger=0.2,
            model_version="v1",
            scored_at=t0 + timedelta(minutes=i * 3),
            escalated=False,
        ))
    for i in range(6, 11):
        m = i % 3
        db.add(McpLlmAxisScore(
            id=i,
            server_id=f"s{i}",
            axis_name=f"axis_{m}",
            label="HIGH",
            label_index=2,
            p_top=0.3,
            p_critical=0.5,
            p_danger=0.2,
            model_version="v2",
            scored_at=t2 + timedelta(minutes=(i - 5) * 4),
            escalated=False,
        ))
    db.commit()
    db.close()

    # ---- Override session ----
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

    # ---- Happy-path test ----
    resp = client.get("/api/scoring_wave_summary")
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    if "waves" not in data or "overall_stats" not in data:
        print(f"FAIL: missing keys in response: {data}", file=sys.stderr)
        sys.exit(1)

    waves = data["waves"]
    # Should have 2 waves (backup_cleanup is not a scoring job).
    if len(waves) != 2:
        print(f"FAIL: expected 2 waves, got {len(waves)}: {[w['job'] for w in waves]}", file=sys.stderr)
        sys.exit(1)

    # Each wave must have non-negative duration.
    for w in waves:
        if not isinstance(w["duration_seconds"], int):
            print(f"FAIL: duration_seconds not int: {type(w['duration_seconds'])}", file=sys.stderr)
            sys.exit(1)
        if w["duration_seconds"] < 0:
            print(f"FAIL: duration_seconds negative: {w['duration_seconds']}", file=sys.stderr)
            sys.exit(1)

    # overall_stats checks
    stats = data["overall_stats"]
    if stats["total_axis_records"] != 10:
        print(f"FAIL: expected 10 axis records, got {stats['total_axis_records']}", file=sys.stderr)
        sys.exit(1)
    if stats["total_servers_scored"] != 10:
        print(f"FAIL: expected 10 servers, got {stats['total_servers_scored']}", file=sys.stderr)
        sys.exit(1)

    # ---- Non-scoring job is excluded ----
    job_names = {w["job"] for w in waves}
    if "backup_cleanup" in job_names:
        print("FAIL: non-scoring job 'backup_cleanup' should be excluded", file=sys.stderr)
        sys.exit(1)

    # ---- Validation: wave_window_hours out of range ----
    resp_bad = client.get("/api/scoring_wave_summary?wave_window_hours=0")
    if resp_bad.status_code != 422:
        print(f"FAIL: expected 422 for wave_window_hours=0, got {resp_bad.status_code}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
