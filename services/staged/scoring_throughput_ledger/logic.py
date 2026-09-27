from collections import defaultdict
from datetime import datetime
from typing import List

import statistics
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/api")


class Period(BaseModel):
    hour: datetime = Field(..., description="Start of the hour bucket (UTC)")
    job: str = Field(..., description="Job name")
    runs: int = Field(..., description="Number of runs in the bucket")
    total_rows: int = Field(..., description="Sum of rows_affected")
    avg_rows_per_run: float = Field(..., description="Average rows_affected per run")
    median_duration_ms: float = Field(..., description="Median duration of runs in milliseconds")


class ThroughputResponse(BaseModel):
    periods: List[Period]


@router.get(
    "/scoring/throughput",
    response_model=ThroughputResponse,
    summary="Scoring throughput ledger",
)
def get_scoring_throughput(session=Depends(get_session)):
    """
    Returns aggregated scoring throughput metrics grouped by job and hour.
    """
    # fetch relevant rows
    rows = (
        session.query(CadenceJobRun)
        .filter(
            (CadenceJobRun.job.like("wave_score_%"))
            | (CadenceJobRun.job == "score_batch")
        )
        .all()
    )

    # aggregate in Python (SQLite does not support percentile_cont)
    buckets = defaultdict(
        lambda: {
            "runs": 0,
            "total_rows": 0,
            "durations_ms": [],
        }
    )

    for row in rows:
        # truncate to hour (UTC)
        hour = row.started_at.replace(minute=0, second=0, microsecond=0)
        key = (hour, row.job)

        # duration in milliseconds
        if row.finished_at and row.started_at:
            duration_ms = (
                (row.finished_at - row.started_at).total_seconds() * 1000
            )
        else:
            duration_ms = 0.0

        bucket = buckets[key]
        bucket["runs"] += 1
        bucket["total_rows"] += row.rows_affected or 0
        bucket["durations_ms"].append(duration_ms)

    periods: List[Period] = []
    for (hour, job), data in buckets.items():
        runs = data["runs"]
        total_rows = data["total_rows"]
        avg_rows = total_rows / runs if runs else 0.0
        median_duration = (
            statistics.median(data["durations_ms"]) if data["durations_ms"] else 0.0
        )
        periods.append(
            Period(
                hour=hour,
                job=job,
                runs=runs,
                total_rows=total_rows,
                avg_rows_per_run=avg_rows,
                median_duration_ms=median_duration,
            )
        )

    # sort chronologically
    periods.sort(key=lambda p: (p.hour, p.job))

    return ThroughputResponse(periods=periods)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create an in‑memory SQLite DB using the real models
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.db import Base  # noqa: E402

    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Populate test data
    now = datetime.datetime.utcnow()
    test_rows = [
        CadenceJobRun(
            job="wave_score_alpha",
            started_at=now - datetime.timedelta(hours=2, minutes=10),
            finished_at=now - datetime.timedelta(hours=2, minutes=5),
            rows_affected=100,
            status="success",
            detail="d1",
        ),
        CadenceJobRun(
            job="wave_score_alpha",
            started_at=now - datetime.timedelta(hours=2, minutes=5),
            finished_at=now - datetime.timedelta(hours=2, minutes=2),
            rows_affected=200,
            status="success",
            detail="d2",
        ),
        CadenceJobRun(
            job="score_batch",
            started_at=now - datetime.timedelta(hours=1, minutes=30),
            finished_at=now - datetime.timedelta(hours=1, minutes=20),
            rows_affected=150,
            status="success",
            detail="d3",
        ),
        CadenceJobRun(
            job="wave_score_beta",
            started_at=now - datetime.timedelta(hours=1, minutes=45),
            finished_at=now - datetime.timedelta(hours=1, minutes=40),
            rows_affected=300,
            status="success",
            detail="d4",
        ),
        CadenceJobRun(
            job="other_job",
            started_at=now - datetime.timedelta(hours=3),
            finished_at=now - datetime.timedelta(hours=2, minutes=55),
            rows_affected=50,
            status="success",
            detail="d5",
        ),
    ]

    sess = TestSession()
    sess.add_all(test_rows)
    sess.commit()
    sess.close()

    # Build FastAPI app with dependency override
    app = FastAPI()
    app.include_router(router)

    def get_test_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)
    response = client.get("/api/scoring/throughput")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["periods"]) >= 3

    # Verify known average for wave_score_alpha
    alpha_period = next(
        (p for p in payload["periods"] if p["job"] == "wave_score_alpha"), None
    )
    assert alpha_period is not None
    assert abs(alpha_period["avg_rows_per_run"] - 150.0) < 1e-6

    print("PASS")