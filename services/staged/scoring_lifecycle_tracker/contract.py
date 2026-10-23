# services/staged/scoring_lifecycle_tracker/contract.py
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

# Real data layer imports (must not be mocked here)
from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api", tags=["scoring_lifecycle"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class WaveEntry(BaseModel):
    date: datetime = Field(..., description="UTC start date of the job (date part)")
    job: str = Field(..., description="Job name")
    status: Optional[str] = Field(None, description="Job status")
    duration_sec: Optional[float] = Field(
        None, description="Duration in seconds (finished_at - started_at)"
    )
    rows_affected: Optional[int] = Field(
        None, description="Number of rows affected by the job"
    )
    rows_per_sec: Optional[float] = Field(
        None, description="Rows affected per second"
    )
    pass_rate: Optional[float] = Field(
        None, description="Completed / total for this job type on the day"
    )


class LifecycleResponse(BaseModel):
    days: int = Field(..., description="Number of days queried")
    waves: List[WaveEntry] = Field(..., description="Scoring job wave details")


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #
SCORING_JOBS = ("score_import", "score_finalize", "score_fire")


def _compute_wave_entries(
    rows: List[CadenceJobRun], cutoff: datetime
) -> List[WaveEntry]:
    """Transform raw CadenceJobRun rows into WaveEntry objects."""
    # Group by (date, job) to compute pass_rate
    aggregates = {}
    for r in rows:
        key_date = r.started_at.date()
        key = (key_date, r.job)
        aggregates.setdefault(key, {"total": 0, "completed": 0, "entries": []})
        aggregates[key]["total"] += 1
        if r.status == "completed":
            aggregates[key]["completed"] += 1

        duration = (
            (r.finished_at - r.started_at).total_seconds()
            if r.finished_at and r.started_at
            else None
        )
        rows_per_sec = (
            r.rows_affected / duration if duration and duration > 0 else None
        )
        entry = WaveEntry(
            date=r.started_at,
            job=r.job,
            status=r.status,
            duration_sec=duration,
            rows_affected=r.rows_affected,
            rows_per_sec=rows_per_sec,
        )
        aggregates[key]["entries"].append(entry)

    # Attach pass_rate to each entry
    result: List[WaveEntry] = []
    for agg in aggregates.values():
        total = agg["total"]
        completed = agg["completed"]
        pass_rate = completed / total if total > 0 else None
        for e in agg["entries"]:
            e.pass_rate = pass_rate
            result.append(e)

    # Sort for deterministic output
    result.sort(key=lambda x: (x.date, x.job))
    return result


@router.get(
    "/scoring/lifecycle",
    response_model=LifecycleResponse,
    summary="Scoring lifecycle tracking",
)
def get_scoring_lifecycle(
    days: int = Query(7, ge=1, description="Number of days to look back"),
    session: Session = Depends(get_session),
):
    """Return scoring job statistics for the past *days* days."""
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)

    stmt = (
        select(CadenceJobRun)
        .where(CadenceJobRun.job.in_(SCORING_JOBS))
        .where(CadenceJobRun.started_at >= cutoff)
    )
    rows = session.execute(stmt).scalars().all()

    if not rows:
        raise HTTPException(status_code=404, detail="No scoring job data found")

    waves = _compute_wave_entries(rows, cutoff)

    return LifecycleResponse(days=days, waves=waves)


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.scoring_lifecycle_tracker.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mimics the real CadenceJobRun table
    # ------------------------------------------------------------------- #
    TEST_DB_URL = "sqlite:///:memory:"
    engine = create_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=engine)

    # Create tables
    CadenceJobRun.__table__.create(bind=engine)

    # Seed test data
    now = datetime.utcnow()
    sample_rows = [
        CadenceJobRun(
            id=1,
            job="score_import",
            status="completed",
            started_at=now - timedelta(hours=5),
            finished_at=now - timedelta(hours=4, minutes=30),
            rows_affected=1200,
            detail="import run",
        ),
        CadenceJobRun(
            id=2,
            job="score_finalize",
            status="failed",
            started_at=now - timedelta(hours=4),
            finished_at=now - timedelta(hours=3, minutes=45),
            rows_affected=0,
            detail="finalize run",
        ),
        CadenceJobRun(
            id=3,
            job="score_fire",
            status="completed",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1, minutes=50),
            rows_affected=3000,
            detail="fire run",
        ),
    ]

    with TestSessionLocal() as db:
        db.add_all(sample_rows)
        db.commit()

    # ------------------------------------------------------------------- #
    # FastAPI app for the test
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    def get_test_session() -> Session:  # pragma: no cover
        return TestSessionLocal()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute request and validate contract
    # ------------------------------------------------------------------- #
    resp = client.get("/api/scoring/lifecycle?days=1")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    waves = data.get("waves", [])
    if len(waves) < 3:
        print("FAIL: expected at least 3 wave entries", file=sys.stderr)
        sys.exit(1)

    if not any(w.get("duration_sec") is not None for w in waves):
        print("FAIL: no duration_sec values present", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)