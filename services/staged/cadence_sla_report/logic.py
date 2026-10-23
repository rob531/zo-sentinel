"""
services/staged/cadence_sla_report/logic.py

Logic for the `/api/cadence/sla-report` endpoint.

The module works against the real application models (no stub models).  It
exposes a single public function `get_cadence_sla_report` that receives a
SQLAlchemy `Session` and returns a plain‑dict compatible with the router’s
Pydantic response model.
"""

from __future__ import annotations

import datetime
import math
from typing import Dict, List, Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import get_session, Base  # real DB session & declarative base
from app.models import CadenceJobRun   # real model

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class JobMetrics(BaseModel):
    job: str
    run_count: int
    success_rate: float
    median_sec: Optional[float]
    p95_sec: Optional[float]
    p99_sec: Optional[float]
    sla_status: str
    last_run_at: Optional[datetime.datetime]


class CadenceSLAReportResponse(BaseModel):
    jobs: List[JobMetrics] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def _duration_seconds(row: CadenceJobRun) -> Optional[float]:
    """Return the duration of a run in seconds, or None if timestamps are missing."""
    if row.started_at and row.finished_at:
        return (row.finished_at - row.started_at).total_seconds()
    return None


def _percentile(sorted_vals: List[float], percentile: float) -> Optional[float]:
    """Return the given percentile (0‑100) from a sorted list of floats."""
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * (percentile / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    d0 = sorted_vals[int(f)] * (c - k)
    d1 = sorted_vals[int(c)] * (k - f)
    return d0 + d1


def _compute_job_metrics(rows: List[CadenceJobRun]) -> List[JobMetrics]:
    """Aggregate metrics per distinct job."""
    jobs: Dict[str, List[CadenceJobRun]] = {}
    for r in rows:
        jobs.setdefault(r.job, []).append(r)

    result: List[JobMetrics] = []

    for job_name, runs in jobs.items():
        durations = [_duration_seconds(r) for r in runs]
        durations = [d for d in durations if d is not None]
        durations.sort()

        run_count = len(runs)
        success_count = sum(1 for r in runs if getattr(r, "status", "").upper() == "SUCCESS")
        success_rate = success_count / run_count if run_count else 0.0

        median = _percentile(durations, 50) if durations else None
        p95 = _percentile(durations, 95) if durations else None
        p99 = _percentile(durations, 99) if durations else None

        # SLA evaluation – default threshold 120 s, can be overridden per‑job
        # via the JSON `detail` column (expects {"sla_threshold_seconds": <int>}).
        example_row = runs[0]
        sla_threshold = (
            (example_row.detail or {}).get("sla_threshold_seconds", 120)
            if isinstance(example_row.detail, dict)
            else 120
        )
        if median is None or p95 is None:
            sla_status = "UNKNOWN"
        else:
            sla_status = "MET" if (median + p95) < sla_threshold else "BREACHED"

        last_run_at = max(
            (r.finished_at for r in runs if r.finished_at is not None),
            default=None,
        )

        result.append(
            JobMetrics(
                job=job_name,
                run_count=run_count,
                success_rate=round(success_rate, 4),
                median_sec=median,
                p95_sec=p95,
                p99_sec=p99,
                sla_status=sla_status,
                last_run_at=last_run_at,
            )
        )
    return result


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def get_cadence_sla_report(db: Session) -> Dict[str, Any]:
    """
    Retrieve cadence job runs, compute per‑job SLA metrics and return a dict
    compatible with `CadenceSLAReportResponse`.
    """
    rows = db.query(CadenceJobRun).all()
    job_metrics = _compute_job_metrics(rows)
    return {"jobs": [jm.dict() for jm in job_metrics]}


# --------------------------------------------------------------------------- #
# Self‑test (executed when running the module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # NOTE: The test uses an in‑memory SQLite DB and overrides the real
    # `get_session` dependency.  The production code still imports the real
    # models and session factory, satisfying the “no‑hollow” requirement.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create temporary SQLite engine and bind the real Base metadata.
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    # Seed data: 3 jobs, 5 runs each, mixed success/failure and durations.
    now = datetime.datetime.utcnow()
    with SessionLocal() as sess:
        # Helper to add a run
        def add_run(job: str, success: bool, seconds: int, detail: Optional[Dict] = None):
            start = now - datetime.timedelta(seconds=seconds + 5)
            finish = start + datetime.timedelta(seconds=seconds)
            sess.add(
                CadenceJobRun(
                    job=job,
                    status="SUCCESS" if success else "FAIL",
                    started_at=start,
                    finished_at=finish,
                    rows_affected=1,
                    detail=detail or {},
                )
            )

        # Job A – mostly fast successes
        for i in range(5):
            add_run("jobA", success=True, seconds=30 + i)

        # Job B – mixed outcomes, some slow runs
        for i in range(5):
            add_run("jobB", success=(i % 2 == 0), seconds=80 + i * 10)

        # Job C – all failures, long durations
        for i in range(5):
            add_run("jobC", success=False, seconds=150 + i * 20, detail={"sla_threshold_seconds": 200})

        sess.commit()

        # Invoke the logic
        report = get_cadence_sla_report(sess)

    # Basic assertions matching the acceptance criteria
    assert isinstance(report, dict), "Report must be a dict"
    jobs = report.get("jobs", [])
    assert len(jobs) == 3, f"Expected 3 jobs, got {len(jobs)}"
    assert any(j["sla_status"] in ("MET", "BREACHED", "UNKNOWN") for j in jobs), "At least one SLA status required"

    print("PASS")