"""logic.py -- cadence_job_duration_stats service data/computation layer.

Provides per‑job run‑duration statistics over the real ``CadenceJobRun`` model.
All data access is performed via a SQLAlchemy ``Session`` from ``app.db``.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CadenceJobRun


def job_duration_stats(db: Session, days: int) -> Dict:
    """Return duration statistics for each distinct Cadence job.

    The query selects runs whose ``started_at`` is within the last ``days``
    (relative to ``datetime.utcnow()``).  Statistics are computed in Python
    using the standard ``statistics`` module.

    Result shape::
        {
            "days": <int>,
            "jobs": [
                {
                    "job": <str>,
                    "runs": <int>,
                    "succeeded": <int>,
                    "failed": <int>,
                    "mean_seconds": <float>,
                    "median_seconds": <float>,
                    "p95_seconds": <float>,
                    "mean_rows_affected": <float>,
                },
                ...
            ],
        }
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    rows = db.execute(
        select(
            CadenceJobRun.job,
            CadenceJobRun.status,
            CadenceJobRun.started_at,
            CadenceJobRun.finished_at,
            CadenceJobRun.rows_affected,
        ).where(CadenceJobRun.started_at >= cutoff)
    ).all()

    # Aggregate raw data per job
    agg: Dict[str, Dict] = {}
    for job, status, started, finished, rows_affected in rows:
        if job not in agg:
            agg[job] = {
                "durations": [],
                "runs": 0,
                "succeeded": 0,
                "failed": 0,
                "rows_total": 0.0,
            }

        entry = agg[job]
        entry["runs"] += 1
        if status == "succeeded":
            entry["succeeded"] += 1
        else:
            entry["failed"] += 1

        # duration in seconds; assume finished is not null
        duration = (finished - started).total_seconds()
        entry["durations"].append(duration)

        # rows_affected may be null; treat null as 0
        entry["rows_total"] += float(rows_affected or 0)

    # Build output list
    jobs_out: List[Dict] = []
    for job, data in agg.items():
        durations = data["durations"]
        durations.sort()
        runs = data["runs"]
        mean_sec = statistics.mean(durations) if durations else 0.0
        median_sec = statistics.median(durations) if durations else 0.0

        # 95th percentile (nearest‑rank method)
        if durations:
            idx = math.ceil(0.95 * len(durations)) - 1
            p95_sec = durations[max(0, min(idx, len(durations) - 1))]
        else:
            p95_sec = 0.0

        mean_rows = data["rows_total"] / runs if runs else 0.0

        jobs_out.append(
            {
                "job": job,
                "runs": runs,
                "succeeded": data["succeeded"],
                "failed": data["failed"],
                "mean_seconds": mean_sec,
                "median_seconds": median_sec,
                "p95_seconds": p95_sec,
                "mean_rows_affected": mean_rows,
            }
        )

    return {"days": days, "jobs": jobs_out}


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from datetime import timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base

    # In‑memory SQLite for the test
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    now = datetime.utcnow().replace(tzinfo=timezone.utc)

    # Seed data: 3 jobs, 4 runs each
    seed = []
    for job_name in ("jobA", "jobB", "jobC"):
        for i, secs in enumerate((10, 20, 30, 40), start=1):
            status = "succeeded" if i <= 2 else "failed"
            rows_affected = i * 100
            started = now - timedelta(seconds=secs + 1000)
            finished = started + timedelta(seconds=secs)
            seed.append(
                CadenceJobRun(
                    job=job_name,
                    status=status,
                    started_at=started,
                    finished_at=finished,
                    rows_affected=rows_affected,
                    detail="test",
                )
            )

    with SessionLocal() as session:
        session.add_all(seed)
        session.commit()

        result = job_duration_stats(session, days=1)

    # Assertions per contract
    assert result["days"] == 1
    assert len(result["jobs"]) == 3

    # Verify known statistics for jobA
    job_a = next(j for j in result["jobs"] if j["job"] == "jobA")
    assert job_a["runs"] == 4
    assert job_a["succeeded"] + job_a["failed"] == job_a["runs"]
    assert math.isclose(job_a["mean_seconds"], 25.0, rel_tol=1e-9)
    assert math.isclose(job_a["median_seconds"], 25.0, rel_tol=1e-9)
    assert math.isclose(job_a["p95_seconds"], 40.0, rel_tol=1e-9)

    print("PASS")