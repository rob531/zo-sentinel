"""logic.py -- data/computation layer for cadence_run_duration_history_api.

Provides a pure function that queries the real CadenceJobRun model via a
SQLAlchemy Session. The router can import and call this function directly,
keeping the endpoint thin and testable.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CadenceJobRun
from app.db import Base, get_session  # get_session is used by the router; Base for __main__ test


def get_cadence_runs(
    db: Session,
    *,
    job: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, object]:
    """Return recent CadenceJobRun rows with aggregated metadata.

    Args:
        db: SQLAlchemy Session.
        job: Optional filter on CadenceJobRun.job.
        limit: Maximum rows to return (clamped to 1‑500, default 50).

    Returns:
        A dict with keys:
            - "runs": List of run dicts.
            - "by_status": Mapping status → count.
            - "job_count": Number of distinct job values in the returned rows.
    """
    # Clamp limit
    limit = max(1, min(limit, 500))

    stmt = select(CadenceJobRun).order_by(CadenceJobRun.started_at.desc()).limit(limit)
    if job is not None:
        stmt = stmt.where(CadenceJobRun.job == job)

    rows: List[CadenceJobRun] = db.execute(stmt).scalars().all()

    runs: List[Dict[str, object]] = []
    status_counter: Counter = Counter()
    distinct_jobs: set = set()

    for r in rows:
        # Compute duration_seconds safely
        duration_seconds: Optional[float] = None
        if r.started_at and r.finished_at and r.finished_at >= r.started_at:
            duration_seconds = (r.finished_at - r.started_at).total_seconds()

        run_dict: Dict[str, object] = {
            "id": r.id,
            "job": r.job,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_seconds": duration_seconds,
            "rows_affected": r.rows_affected,
        }
        runs.append(run_dict)

        status_counter[r.status or "null"] += 1
        distinct_jobs.add(r.job)

    by_status: Dict[str, int] = {status: int(cnt) for status, cnt in status_counter.items()}

    return {
        "runs": runs,
        "by_status": by_status,
        "job_count": len(distinct_jobs),
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the self‑test
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as session:
        # Seed data: 4 rows
        now = datetime(2023, 1, 1, 12, 0, 0)

        # Row 1: finished, known 30‑second duration
        row1 = CadenceJobRun(
            id=1,
            job="jobA",
            status="completed",
            started_at=now,
            finished_at=now + timedelta(seconds=30),
            rows_affected=10,
            detail=None,
        )
        # Row 2: unfinished (NULL finished_at)
        row2 = CadenceJobRun(
            id=2,
            job="jobB",
            status="running",
            started_at=now + timedelta(minutes=1),
            finished_at=None,
            rows_affected=5,
            detail=None,
        )
        # Row 3: same status as row2
        row3 = CadenceJobRun(
            id=3,
            job="jobC",
            status="running",
            started_at=now + timedelta(minutes=2),
            finished_at=None,
            rows_affected=7,
            detail=None,
        )
        # Row 4: another completed row, different job
        row4 = CadenceJobRun(
            id=4,
            job="jobA",
            status="completed",
            started_at=now + timedelta(minutes=3),
            finished_at=now + timedelta(minutes=3, seconds=15),
            rows_affected=12,
            detail=None,
        )
        session.add_all([row1, row2, row3, row4])
        session.commit()

        # Default call (limit 50)
        result = get_cadence_runs(session)
        runs = result["runs"]
        by_status = result["by_status"]
        job_count = result["job_count"]

        assert len(runs) == 4, "expected 4 runs"
        # Runs are ordered newest first
        # Find the known 30‑second row (id=1)
        dur_row = next(r for r in runs if r["id"] == 1)
        assert dur_row["duration_seconds"] == 30.0, "duration mismatch"
        # Row with NULL finished_at (id=2) should have None duration
        null_row = next(r for r in runs if r["id"] == 2)
        assert null_row["duration_seconds"] is None, "expected None duration"

        # by_status counts sum to 4
        assert sum(by_status.values()) == 4, "status counts mismatch"

        # Limit truncation test
        limited = get_cadence_runs(session, limit=2)
        assert len(limited["runs"]) == 2, "limit truncation failed"

        print("PASS")