"""logic.py -- data/computation layer for the cadence_job_backlog service.

Provides a single pure function that aggregates CadenceJobRun records into a
backlog summary. Mirrors the exemplar pattern: imports the real data layer
(app.db, app.models) and operates over a SQLAlchemy Session.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import CadenceJobRun

SUCCESS_STATUSES = {"success", "completed"}


def get_cadence_backlog(db: Session, max_age_seconds: int = 3600) -> Dict:
    """Return backlog summary for all distinct cadence jobs.

    Args:
        db: SQLAlchemy Session.
        max_age_seconds: Age threshold (seconds) for a job to be considered stale.

    Returns:
        Dict matching the CadenceBacklogResponse schema.
    """
    now = datetime.utcnow()
    cutoff_24h = now - timedelta(hours=24)

    # Load all runs; the dataset is expected to be modest.
    runs: List[CadenceJobRun] = db.execute(select(CadenceJobRun)).scalars().all()

    jobs: Dict[str, Dict] = {}
    for run in runs:
        job_name = run.job
        if job_name not in jobs:
            jobs[job_name] = {
                "runs_24h": 0,
                "failed_24h": 0,
                "last_run": None,  # will hold the most recent CadenceJobRun
            }

        # Count runs within the last 24h
        if run.started_at >= cutoff_24h:
            jobs[job_name]["runs_24h"] += 1
            if run.status not in SUCCESS_STATUSES:
                jobs[job_name]["failed_24h"] += 1

        # Track the most recent run (by started_at)
        last = jobs[job_name]["last_run"]
        if last is None or (run.started_at and run.started_at > last.started_at):
            jobs[job_name]["last_run"] = run

    job_list = []
    stale_jobs = 0
    for name, data in jobs.items():
        last = data["last_run"]
        duration_seconds = (
            (last.finished_at - last.started_at).total_seconds()
            if last.finished_at and last.started_at
            else None
        )
        stale = (now - last.started_at).total_seconds() > max_age_seconds if last.started_at else False
        if stale:
            stale_jobs += 1

        runs_24h = data["runs_24h"]
        failed_24h = data["failed_24h"]
        success_rate = (
            (runs_24h - failed_24h) / runs_24h if runs_24h > 0 else None
        )

        job_list.append(
            {
                "job": name,
                "runs_24h": runs_24h,
                "failed_24h": failed_24h,
                "success_rate": success_rate,
                "last_status": last.status,
                "last_started_at": last.started_at,
                "last_finished_at": last.finished_at,
                "last_rows_affected": last.rows_affected,
                "duration_seconds": duration_seconds,
                "stale": stale,
            }
        )

    return {
        "jobs": job_list,
        "total_jobs": len(job_list),
        "stale_jobs": stale_jobs,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the self‑test
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    now = datetime.utcnow()
    with SessionLocal() as session:
        # Job A: two successful runs within 24h
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="jobA",
                    status="success",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(hours=0, minutes=55),
                    rows_affected=10,
                    detail="ok",
                ),
                CadenceJobRun(
                    id=2,
                    job="jobA",
                    status="success",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=1, minutes=55),
                    rows_affected=5,
                    detail="ok",
                ),
                # Job B: one failing run (stale) and one old successful run (>24h)
                CadenceJobRun(
                    id=3,
                    job="jobB",
                    status="failure",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=1, minutes=50),
                    rows_affected=0,
                    detail="failed",
                ),
                CadenceJobRun(
                    id=4,
                    job="jobB",
                    status="success",
                    started_at=now - timedelta(hours=25),
                    finished_at=now - timedelta(hours=24, minutes=55),
                    rows_affected=8,
                    detail="old ok",
                ),
            ]
        )
        session.commit()

        result = get_cadence_backlog(session)

        # Assertions per acceptance criteria
        assert result["total_jobs"] == 2, "total_jobs should be 2"
        # Find jobB entry
        jobb = next(j for j in result["jobs"] if j["job"] == "jobB")
        assert jobb["failed_24h"] == 1, "jobB failed_24h should be 1"
        assert jobb["stale"] is True, "jobB should be marked stale"
        print("PASS")