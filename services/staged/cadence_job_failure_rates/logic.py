"""services/staged/cadence_job_failure_rates/logic.py

Data/computation layer for the cadence_job_failure_rates service.

Provides a pure function that operates on a SQLAlchemy Session and returns
the failure‑rate statistics required by the router.  The module also contains
a self‑test that runs when executed as ``__main__``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CadenceJobRun


def _aggregate_rows(
    rows: List[CadenceJobRun],
) -> List[Dict[str, object]]:
    """Aggregate a list of CadenceJobRun rows into per‑job statistics."""
    per_job: Dict[str, Dict[str, object]] = {}

    for row in rows:
        job = row.job
        if job not in per_job:
            per_job[job] = {
                "job": job,
                "total_runs": 0,
                "success_runs": 0,
                "failed_runs": 0,
                "duration_sum": 0.0,
                "duration_count": 0,
                "last_started_at": None,  # datetime
                "last_status": None,      # str
            }

        stats = per_job[job]
        stats["total_runs"] += 1
        if row.status == "success":
            stats["success_runs"] += 1
        elif row.status in ("failed", "error"):
            stats["failed_runs"] += 1

        # duration
        if row.started_at is not None and row.finished_at is not None:
            dur = (row.finished_at - row.started_at).total_seconds()
            stats["duration_sum"] += dur
            stats["duration_count"] += 1

        # last run tracking
        if (
            stats["last_started_at"] is None
            or row.started_at > stats["last_started_at"]
        ):
            stats["last_started_at"] = row.started_at
            stats["last_status"] = row.status

    out: List[Dict[str, object]] = []
    for stats in per_job.values():
        total = stats["total_runs"]
        failed = stats["failed_runs"]
        failure_rate = failed / total if total else 0.0

        avg_duration: Optional[float] = None
        if stats["duration_count"]:
            avg_duration = stats["duration_sum"] / stats["duration_count"]

        out.append(
            {
                "job": stats["job"],
                "total_runs": total,
                "success_runs": stats["success_runs"],
                "failed_runs": failed,
                "failure_rate": failure_rate,
                "avg_duration_seconds": avg_duration,
                "last_status": stats["last_status"],
                "last_started_at": (
                    stats["last_started_at"].isoformat()
                    if stats["last_started_at"]
                    else None
                ),
            }
        )
    return out


def get_failure_rates(db: Session, days: int) -> Dict[str, object]:
    """
    Compute failure‑rate statistics for Cadence jobs over the past ``days`` days.

    Returns a mapping compatible with the router's response model:
    {
        "days": <int>,
        "jobs": [
            {
                "job": <str>,
                "total_runs": <int>,
                "success_runs": <int>,
                "failed_runs": <int>,
                "failure_rate": <float>,
                "avg_duration_seconds": <float | None>,
                "last_status": <str | None>,
                "last_started_at": <ISO‑8601 str | None>,
            },
            ...
        ],
    }
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    stmt = (
        select(CadenceJobRun)
        .where(CadenceJobRun.started_at >= cutoff)
        .order_by(CadenceJobRun.started_at.asc())
    )
    rows = db.execute(stmt).scalars().all()

    jobs = _aggregate_rows(rows)

    return {"days": days, "jobs": jobs}


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite engine – isolated from the real application DB.
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    # Create the CadenceJobRun table in the temporary DB.
    CadenceJobRun.__table__.create(engine)

    now = datetime.utcnow()
    with SessionLocal() as db:
        # Seed 6 rows across two jobs.
        rows = [
            # Job A – 2 runs, 1 success, 1 failed → failure_rate 0.5
            CadenceJobRun(
                job="job_a",
                status="success",
                started_at=now - timedelta(hours=5),
                finished_at=now - timedelta(hours=4, minutes=55),
                rows_affected=10,
                detail={},
            ),
            CadenceJobRun(
                job="job_a",
                status="failed",
                started_at=now - timedelta(hours=3),
                finished_at=now - timedelta(hours=2, minutes=58),
                rows_affected=0,
                detail={},
            ),
            # Job B – 4 runs, all success → failure_rate 0.0
            CadenceJobRun(
                job="job_b",
                status="success",
                started_at=now - timedelta(days=1, hours=2),
                finished_at=now - timedelta(days=1, hours=1, minutes=55),
                rows_affected=5,
                detail={},
            ),
            CadenceJobRun(
                job="job_b",
                status="success",
                started_at=now - timedelta(days=1, hours=1),
                finished_at=now - timedelta(days=1, minutes=55),
                rows_affected=7,
                detail={},
            ),
            CadenceJobRun(
                job="job_b",
                status="success",
                started_at=now - timedelta(hours=1, minutes=30),
                finished_at=now - timedelta(hours=1, minutes=25),
                rows_affected=3,
                detail={},
            ),
            CadenceJobRun(
                job="job_b",
                status="success",
                started_at=now - timedelta(minutes=20),
                finished_at=now - timedelta(minutes=15),
                rows_affected=2,
                detail={},
            ),
        ]

        for r in rows:
            db.add(r)
        db.commit()

        result = get_failure_rates(db, days=7)

        # Assertions per acceptance criteria.
        assert result["days"] == 7
        job_a = next(j for j in result["jobs"] if j["job"] == "job_a")
        job_b = next(j for j in result["jobs"] if j["job"] == "job_b")

        assert abs(job_a["failure_rate"] - 0.5) < 1e-9, "job_a failure_rate should be 0.5"
        assert job_b["failure_rate"] == 0.0, "job_b failure_rate should be 0.0"
        assert (
            job_b["avg_duration_seconds"] is not None
            and job_b["avg_duration_seconds"] > 0
        ), "avg_duration_seconds must be > 0 for job_b"

        print("PASS")