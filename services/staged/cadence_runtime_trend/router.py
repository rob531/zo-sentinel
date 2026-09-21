from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = APIRouter(prefix="/api/cadence", tags=["cadence"])


class JobRuntimeTrend(BaseModel):
    job_name: str
    run_count: int
    avg_duration_s: float
    success_rate_pct: float
    rows_per_run_avg: float
    last_run_at: Optional[datetime]


class RuntimeTrendResponse(BaseModel):
    window_days: int
    jobs: list[JobRuntimeTrend]


@router.get("/runtime-trend", response_model=RuntimeTrendResponse)
async def get_runtime_trend(
    window_days: int = 7,
    session: AsyncSession = Depends(get_session),
) -> RuntimeTrendResponse:
    """
    Returns aggregated runtime trends per job over the configured N-day window.
    Defaults to 7-day window.
    """
    cutoff_date = datetime.now() - timedelta(days=window_days)

    query = text("""
        WITH job_stats AS (
            SELECT
                job,
                COUNT(*) as run_count,
                AVG(
                    CASE
                        WHEN finished_at IS NOT NULL AND started_at IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (finished_at - started_at))
                        ELSE NULL
                    END
                ) as avg_duration_s,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END)::float / COUNT(*) * 100 as success_rate_pct,
                AVG(rows_affected) as rows_per_run_avg,
                MAX(started_at) as last_run_at
            FROM cadence_job_runs
            WHERE started_at >= :cutoff_date
            GROUP BY job
        )
        SELECT
            job as job_name,
            run_count,
            COALESCE(avg_duration_s, 0.0) as avg_duration_s,
            COALESCE(success_rate_pct, 0.0) as success_rate_pct,
            COALESCE(rows_per_run_avg, 0.0) as rows_per_run_avg,
            last_run_at
        FROM job_stats
        ORDER BY job_name
    """)

    result = await session.execute(query, {"cutoff_date": cutoff_date})
    rows = result.fetchall()

    jobs = []
    for row in rows:
        jobs.append(
            JobRuntimeTrend(
                job_name=row.job_name,
                run_count=row.run_count,
                avg_duration_s=round(float(row.avg_duration_s) if row.avg_duration_s else 0.0, 2),
                success_rate_pct=round(float(row.success_rate_pct) if row.success_rate_pct else 0.0, 2),
                rows_per_run_avg=round(float(row.rows_per_run_avg) if row.rows_per_run_avg else 0.0, 2),
                last_run_at=row.last_run_at,
            )
        )

    return RuntimeTrendResponse(window_days=window_days, jobs=jobs)


if __name__ == "__main__":
    import asyncio
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from datetime import datetime, timedelta

    # In-memory SQLite for self-test
    engine = create_engine("sqlite:///:memory:", echo=False)
    metadata = None

    from sqlalchemy import Table, Column, String, Float, DateTime, Integer, MetaData

    metadata = MetaData()
    cadence_job_runs = Table(
        "cadence_job_runs",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("job", String),
        Column("status", String),
        Column("started_at", DateTime),
        Column("finished_at", DateTime),
        Column("rows_affected", Float),
        Column("detail", String),
    )
    metadata.create_all(engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed 4 jobs with 6 runs each across 3 days
    jobs = ["job_alpha", "job_beta", "job_gamma", "job_delta"]
    base_time = datetime.now() - timedelta(days=3)

    for job_idx, job_name in enumerate(jobs):
        for run_idx in range(6):
            offset = timedelta(hours=run_idx * 4, days=run_idx // 3)
            started = base_time + offset + timedelta(hours=job_idx)
            finished = started + timedelta(seconds=100 + job_idx * 10 + run_idx * 2)
            status = "success" if run_idx % 5 != 0 else "fail"
            rows = 1000.0 + job_idx * 100 + run_idx * 50

            session.execute(
                cadence_job_runs.insert().values(
                    job=job_name,
                    status=status,
                    started_at=started,
                    finished_at=finished,
                    rows_affected=rows,
                    detail='{"test": true}',
                )
            )
    session.commit()

    # Override dependency
    from app.db import get_session

    async def override_get_session():
        yield session

    from main import app

    app.dependency_overrides[get_session] = override_get_session

    # Run test
    async def run_test():
        from fastapi.testclient import TestClient

        client = TestClient(app)
        response = client.get("/api/cadence/runtime-trend?window_days=7")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        data = response.json()
        assert "window_days" in data
        assert "jobs" in data
        assert len(data["jobs"]) == 4, f"Expected 4 jobs, got {len(data['jobs'])}"

        # Find job_alpha to check avg_duration_s
        job_alpha = next((j for j in data["jobs"] if j["job_name"] == "job_alpha"), None)
        assert job_alpha is not None, "job_alpha not found"
        assert job_alpha["run_count"] == 6, f"Expected 6 runs for job_alpha, got {job_alpha['run_count']}"

        # Check that avg_duration_s is a reasonable value (should be around 110s for job_alpha)
        assert job_alpha["avg_duration_s"] > 0, "Expected positive avg_duration_s"
        assert job_alpha["success_rate_pct"] > 0, "Expected positive success_rate_pct"

        print("PASS")

    asyncio.run(run_test())