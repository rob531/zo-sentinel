"""logic.py -- data/computation layer for the cadence_job_timeline service.

Provides a single pure function that queries the real ``CadenceJobRun`` model
via a SQLAlchemy ``Session``.  The router layer can import and call this
function; the __main__ block runs a self‑test using an in‑memory SQLite DB
that mimics the real schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import CadenceJobRun


def get_cadence_job_timeline(
    *,
    db: Session,
    job: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Return recent runs and per‑job summary.

    Args:
        db: SQLAlchemy session.
        job: Optional job name to filter on.
        limit: Maximum number of recent runs to return (clamped 1‑500).

    Returns:
        Mapping with keys:
            - ``jobs``: list of per‑job summary dicts.
            - ``runs``: list of recent run dicts (ordered newest first).
            - ``total_runs``: total number of runs matching the filter.
    """
    # Clamp limit server‑side.
    limit = max(1, min(limit, 500))

    # Base filter.
    base_filter = []
    if job is not None:
        base_filter.append(CadenceJobRun.job == job)

    # ------------------------------------------------------------------
    # Fetch *all* runs matching the filter for summary calculations.
    # ------------------------------------------------------------------
    all_runs_stmt = select(CadenceJobRun).where(*base_filter).order_by(
        CadenceJobRun.started_at.desc()
    )
    all_runs: List[CadenceJobRun] = db.scalars(all_runs_stmt).all()
    total_runs = len(all_runs)

    # ------------------------------------------------------------------
    # Build per‑job aggregates.
    # ------------------------------------------------------------------
    job_stats: Dict[str, Dict[str, Any]] = {}
    for run in all_runs:
        stats = job_stats.setdefault(
            run.job,
            {
                "job": run.job,
                "run_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "last_started_at": None,
                "last_status": None,
                "duration_sum": 0.0,
                "duration_count": 0,
                "total_rows_affected": 0,
            },
        )
        stats["run_count"] += 1
        if run.status == "success":
            stats["success_count"] += 1
        else:
            stats["failure_count"] += 1

        if (
            stats["last_started_at"] is None
            or run.started_at > stats["last_started_at"]
        ):
            stats["last_started_at"] = run.started_at
            stats["last_status"] = run.status

        if run.finished_at is not None:
            dur = (run.finished_at - run.started_at).total_seconds()
            stats["duration_sum"] += dur
            stats["duration_count"] += 1

        if run.rows_affected is not None:
            stats["total_rows_affected"] += run.rows_affected

    jobs_summary: List[Dict[str, Any]] = []
    for s in job_stats.values():
        avg_dur = (
            s["duration_sum"] / s["duration_count"]
            if s["duration_count"] > 0
            else None
        )
        jobs_summary.append(
            {
                "job": s["job"],
                "run_count": s["run_count"],
                "success_count": s["success_count"],
                "failure_count": s["failure_count"],
                "last_started_at": s["last_started_at"],
                "last_status": s["last_status"],
                "avg_duration_seconds": avg_dur,
                "total_rows_affected": s["total_rows_affected"],
            }
        )

    # ------------------------------------------------------------------
    # Build recent runs list (limited).
    # ------------------------------------------------------------------
    recent_runs = all_runs[:limit]
    runs_out: List[Dict[str, Any]] = []
    for run in recent_runs:
        duration_seconds = (
            (run.finished_at - run.started_at).total_seconds()
            if run.finished_at is not None
            else None
        )
        runs_out.append(
            {
                "job": run.job,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "duration_seconds": duration_seconds,
                "rows_affected": run.rows_affected,
                "detail": run.detail,
            }
        )

    return {
        "jobs": jobs_summary,
        "runs": runs_out,
        "total_runs": total_runs,
    }


# ----------------------------------------------------------------------
# Self‑test when executed as a script.
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite engine mimicking the real schema.
    engine = create_engine("sqlite:///:memory:", future=True)
    from app.db import Base  # type: ignore

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    # Seed data.
    sess = SessionLocal()
    try:
        sess.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="scanner",
                    status="success",
                    started_at=datetime(2023, 1, 1, 10, 0, 0),
                    finished_at=datetime(2023, 1, 1, 10, 5, 0),
                    rows_affected=123,
                    detail={"info": "ok"},
                ),
                CadenceJobRun(
                    id=2,
                    job="scanner",
                    status="failure",
                    started_at=datetime(2023, 1, 1, 11, 0, 0),
                    finished_at=datetime(2023, 1, 1, 11, 3, 0),
                    rows_affected=0,
                    detail={"error": "boom"},
                ),
                CadenceJobRun(
                    id=3,
                    job="enricher",
                    status="success",
                    started_at=datetime(2023, 1, 2, 9, 30, 0),
                    finished_at=datetime(2023, 1, 2, 9, 45, 0),
                    rows_affected=45,
                    detail={"info": "enriched"},
                ),
            ]
        )
        sess.commit()

        result = get_cadence_job_timeline(db=sess, limit=10)

        # Assertions per acceptance criteria.
        assert isinstance(result, dict)
        assert len(result["jobs"]) == 2, "expected two distinct jobs"
        scanner_summary = next(j for j in result["jobs"] if j["job"] == "scanner")
        assert scanner_summary["success_count"] == 1, "scanner success count"
        assert scanner_summary["failure_count"] == 1, "scanner failure count"

        # Verify a completed run has positive duration.
        completed = next(
            r for r in result["runs"] if r["finished_at"] is not None
        )
        assert (
            completed["duration_seconds"] is not None
            and completed["duration_seconds"] > 0
        ), "duration_seconds should be positive for completed runs"

        print("PASS")
    finally:
        sess.close()