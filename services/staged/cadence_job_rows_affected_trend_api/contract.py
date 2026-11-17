"""cadence_job_rows_affected_trend_api contract.

Provides a FastAPI router that returns per‑job and per‑day rows‑affected
statistics for Cadence jobs.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Real data layer imports – do NOT create a custom session here.
from app.db import get_session, Base
from app.models import CadenceJobRun

router = APIRouter(prefix="/api", tags=["cadence_job_rows_affected_trend"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #
class JobStats(BaseModel):
    job: str
    runs: int
    succeeded: int
    failed: int
    rows_affected_total: int
    rows_affected_max: int


class SeriesItem(BaseModel):
    date: str = Field(..., description="ISO‑format calendar day (YYYY‑MM‑DD)")
    job: str
    rows_affected: int
    runs: int


class CadenceJobRowsAffectedTrendResponse(BaseModel):
    days: int
    jobs: List[JobStats]
    series: List[SeriesItem]


# --------------------------------------------------------------------------- #
# Endpoint implementation
# --------------------------------------------------------------------------- #
@router.get(
    "/cadence/jobs/rows-affected",
    response_model=CadenceJobRowsAffectedTrendResponse,
    summary="Rows‑affected trend for Cadence jobs",
)
def get_rows_affected_trend(
    days: int = Query(
        14,
        ge=1,
        le=90,
        description="Number of days to look back (default 14, max 90)",
    ),
    job: Optional[str] = Query(
        None, description="If supplied, restrict results to this job name"
    ),
    session: Session = Depends(get_session),
) -> CadenceJobRowsAffectedTrendResponse:
    """Return rows‑affected statistics for Cadence jobs.

    The query is performed purely in Python – no DB date functions are used,
    keeping the SQL portable across back‑ends.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    query = session.query(CadenceJobRun).filter(CadenceJobRun.started_at >= cutoff)
    if job:
        query = query.filter(CadenceJobRun.job == job)

    rows: List[CadenceJobRun] = query.all()

    # ------------------------------------------------------------------- #
    # Aggregate per‑job statistics
    # ------------------------------------------------------------------- #
    job_agg: Dict[str, Dict[str, int]] = {}
    # (date, job) -> {"rows_affected": int, "runs": int}
    series_agg: Dict[Tuple[str, str], Dict[str, int]] = {}

    for r in rows:
        job_key = r.job
        # Initialise per‑job bucket
        if job_key not in job_agg:
            job_agg[job_key] = {
                "runs": 0,
                "succeeded": 0,
                "failed": 0,
                "rows_affected_total": 0,
                "rows_affected_max": 0,
            }

        stats = job_agg[job_key]
        stats["runs"] += 1
        if (r.status or "").lower() == "succeeded":
            stats["succeeded"] += 1
        else:
            stats["failed"] += 1

        rows_aff = r.rows_affected or 0
        stats["rows_affected_total"] += rows_aff
        if rows_aff > stats["rows_affected_max"]:
            stats["rows_affected_max"] = rows_aff

        # Series aggregation
        date_str = (r.started_at.date()).isoformat()
        series_key = (date_str, job_key)
        if series_key not in series_agg:
            series_agg[series_key] = {"rows_affected": 0, "runs": 0}
        series_agg[series_key]["rows_affected"] += rows_aff
        series_agg[series_key]["runs"] += 1

    jobs_list = [
        JobStats(
            job=j,
            runs=v["runs"],
            succeeded=v["succeeded"],
            failed=v["failed"],
            rows_affected_total=v["rows_affected_total"],
            rows_affected_max=v["rows_affected_max"],
        )
        for j, v in sorted(job_agg.items())
    ]

    series_list = [
        SeriesItem(
            date=dt,
            job=j,
            rows_affected=info["rows_affected"],
            runs=info["runs"],
        )
        for (dt, j), info in sorted(series_agg.items())
    ]

    return CadenceJobRowsAffectedTrendResponse(
        days=days,
        jobs=jobs_list,
        series=series_list,
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed via `python -m services.staged.cadence_job_rows_affected_trend_api.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB dependency)
    # ------------------------------------------------------------------- #
    SQLITE_URL = "sqlite://"
    engine = create_engine(
        SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Create tables for the models we use
    Base.metadata.create_all(bind=engine)

    # Dependency override
    def _override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # FastAPI app wiring
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    # ------------------------------------------------------------------- #
    # Seed test data (6 rows, 2 jobs, 2 days)
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    day1 = now - timedelta(days=1)
    day2 = now - timedelta(days=2)

    seed_rows = [
        CadenceJobRun(
            job="job_alpha",
            status="succeeded",
            started_at=day1,
            finished_at=day1 + timedelta(minutes=1),
            rows_affected=10,
            detail="seed 1",
        ),
        CadenceJobRun(
            job="job_alpha",
            status="failed",
            started_at=day1,
            finished_at=day1 + timedelta(minutes=2),
            rows_affected=5,
            detail="seed 2",
        ),
        CadenceJobRun(
            job="job_alpha",
            status="succeeded",
            started_at=day2,
            finished_at=day2 + timedelta(minutes=1),
            rows_affected=7,
            detail="seed 3",
        ),
        CadenceJobRun(
            job="job_beta",
            status="succeeded",
            started_at=day1,
            finished_at=day1 + timedelta(minutes=3),
            rows_affected=3,
            detail="seed 4",
        ),
        CadenceJobRun(
            job="job_beta",
            status="failed",
            started_at=day2,
            finished_at=day2 + timedelta(minutes=2),
            rows_affected=4,
            detail="seed 5",
        ),
        CadenceJobRun(
            job="job_beta",
            status="succeeded",
            started_at=day2,
            finished_at=day2 + timedelta(minutes=3),
            rows_affected=2,
            detail="seed 6",
        ),
    ]

    # Insert seed data
    with SessionLocal() as db:
        db.add_all(seed_rows)
        db.commit()

    # ------------------------------------------------------------------- #
    # Run test client
    # ------------------------------------------------------------------- #
    client = TestClient(app)

    resp = client.get("/api/cadence/jobs/rows-affected")
    if resp.status_code != 200:
        print(f"❌ Unexpected status code: {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    payload = resp.json()

    # Assertions per acceptance criteria
    assert payload["days"] == 14, "default days should be 14"
    assert len(payload["jobs"]) == 2, "should report two distinct jobs"

    # Find job_alpha stats
    job_alpha = next((j for j in payload["jobs"] if j["job"] == "job_alpha"), None)
    assert job_alpha is not None, "job_alpha missing in response"
    expected_alpha_total = 10 + 5 + 7
    assert (
        job_alpha["rows_affected_total"] == expected_alpha_total
    ), f"job_alpha rows_affected_total expected {expected_alpha_total}"

    print("PASS")
    sys.exit(0)