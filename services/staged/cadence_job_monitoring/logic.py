"""
services/staged/cadence_job_monitoring/logic.py

Logic for the `cadence_job_monitoring` service.

Provides:
    - GET /api/cadence/jobs?status=<status>
      Returns job monitoring information aggregated from the
      `CadenceJobRun` model.

The module mirrors the structure of `services/_exemplar/logic.py` and
uses the real application data layer (`app.db`, `app.models`).
"""

from datetime import datetime
from typing import List, Optional, Dict

from fastapi import Depends, Query
from pydantic import BaseModel, Field

from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.db import get_session
from app.models import CadenceJobRun  # type: ignore  # real model import


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class RecentRun(BaseModel):
    """Compact representation of a recent Cadence job run."""
    id: int
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    rows_affected: Optional[int] = None


class JobInfo(BaseModel):
    """Full representation of a Cadence job with recent runs."""
    job: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    rows_affected: Optional[int] = None
    detail: Optional[str] = None
    recent_runs: List[RecentRun] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #
def fetch_cadence_jobs(
    status: Optional[str] = Query(None, description="Filter by job status"),
    db: Session = Depends(get_session),
) -> List[JobInfo]:
    """
    Retrieve Cadence job monitoring information.

    - If ``status`` is supplied, only runs matching that status are considered.
    - Results are grouped by ``job`` name.
    - For each job the most recent run supplies the top‑level fields.
    - ``recent_runs`` contains up to the five most recent runs for that job.

    Returns a list of :class:`JobInfo` objects.
    """
    query = db.query(CadenceJobRun)
    if status:
        query = query.filter(CadenceJobRun.status == status)

    # Order by start time descending so the first row per job is the latest.
    runs = query.order_by(desc(CadenceJobRun.started_at)).all()

    # Group runs by job name.
    grouped: Dict[str, List[CadenceJobRun]] = {}
    for run in runs:
        grouped.setdefault(run.job, []).append(run)

    result: List[JobInfo] = []
    for job_name, job_runs in grouped.items():
        latest = job_runs[0]  # most recent due to ordering
        recent = [
            RecentRun(
                id=r.id,
                status=r.status,
                started_at=r.started_at,
                finished_at=r.finished_at,
                rows_affected=r.rows_affected,
            )
            for r in job_runs[:5]  # up to five recent runs
        ]

        result.append(
            JobInfo(
                job=latest.job,
                status=latest.status,
                started_at=latest.started_at,
                finished_at=latest.finished_at,
                rows_affected=latest.rows_affected,
                detail=getattr(latest, "detail", None),
                recent_runs=recent,
            )
        )
    return result


# --------------------------------------------------------------------------- #
# Self‑test (executed when running the module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # The self‑test uses an in‑memory SQLite database and overrides the
    # ``get_session`` dependency with a temporary session.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base  # type: ignore  # Base metadata for table creation

    # ------------------------------------------------------------------- #
    # Setup in‑memory SQLite and create tables
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)
    Base.metadata.create_all(bind=engine)

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    session: Session = SessionLocal()
    now = datetime.utcnow()
    runs = [
        CadenceJobRun(
            job="test_job",
            status="completed",
            started_at=now,
            finished_at=now,
            rows_affected=10,
            detail="first run",
        ),
        CadenceJobRun(
            job="test_job",
            status="running",
            started_at=now,
            finished_at=None,
            rows_affected=5,
            detail="second run",
        ),
        CadenceJobRun(
            job="test_job",
            status="failed",
            started_at=now,
            finished_at=now,
            rows_affected=0,
            detail="third run",
        ),
    ]
    session.add_all(runs)
    session.commit()

    # ------------------------------------------------------------------- #
    # Invoke core logic directly (bypassing FastAPI dependency injection)
    # ------------------------------------------------------------------- #
    result = fetch_cadence_jobs(status="completed", db=session)

    # ------------------------------------------------------------------- #
    # Assertions matching the acceptance criteria
    # ------------------------------------------------------------------- #
    assert isinstance(result, list), "Result should be a list"
    assert len(result) == 1, "Exactly one job should be returned"
    job_info = result[0]
    assert job_info.job == "test_job", "Job name mismatch"
    assert job_info.status == "completed", "Status filter not applied"
    assert len(job_info.recent_runs) == 1, "Recent runs length mismatch"
    assert job_info.recent_runs[0].status == "completed", "Recent run status mismatch"

    print("PASS")