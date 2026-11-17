from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


router = APIRouter(prefix="/api")


@router.get("/cadence/rows-affected-leaderboard")
def rows_affected_leaderboard(
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(get_session),
) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    result = db.execute(
        text(
            """
            WITH filtered AS (
                SELECT id, job, status, rows_affected, started_at
                FROM cadence_job_runs
                WHERE started_at >= :cutoff
            ),
            summary AS (
                SELECT
                    job,
                    COUNT(*) AS run_count,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failure_count,
                    COALESCE(SUM(rows_affected), 0) AS total_rows_affected,
                    CAST(SUM(rows_affected) AS REAL) / NULLIF(COUNT(*), 0)
                        AS avg_rows_affected,
                    MAX(started_at) AS last_started_at
                FROM filtered
                GROUP BY job
            )
            SELECT
                summary.job,
                summary.run_count,
                summary.success_count,
                summary.failure_count,
                summary.total_rows_affected,
                summary.avg_rows_affected,
                summary.last_started_at,
                (
                    SELECT filtered_latest.status
                    FROM filtered AS filtered_latest
                    WHERE filtered_latest.job = summary.job
                    ORDER BY filtered_latest.started_at DESC, filtered_latest.id DESC
                    LIMIT 1
                ) AS last_status
            FROM summary
            ORDER BY summary.total_rows_affected DESC, summary.job ASC
            """
        ),
        {"cutoff": cutoff},
    )

    jobs = [
        {
            "job": row["job"],
            "run_count": row["run_count"],
            "success_count": row["success_count"],
            "failure_count": row["failure_count"],
            "total_rows_affected": row["total_rows_affected"],
            "avg_rows_affected": row["avg_rows_affected"],
            "last_started_at": row["last_started_at"],
            "last_status": row["last_status"],
        }
        for row in result.mappings()
    ]

    return {
        "days": days,
        "jobs": jobs,
        "job_count": len(jobs),
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE cadence_job_runs (
                    id INTEGER PRIMARY KEY,
                    job TEXT NOT NULL,
                    status TEXT NOT NULL,
                    rows_affected INTEGER,
                    started_at DATETIME NOT NULL,
                    finished_at DATETIME,
                    detail TEXT
                )
                """
            )
        )

    now = datetime.now(timezone.utc)
    with TestingSession() as test_session:
        test_session.execute(
            text(
                """
                INSERT INTO cadence_job_runs
                    (id, job, status, rows_affected, started_at, finished_at, detail)
                VALUES
                    (1, 'alpha', 'success', 3, :first_started, :first_finished, 'ok'),
                    (2, 'alpha', 'success', 4, :second_started, :second_finished, 'ok'),
                    (3, 'beta', 'failed', 5, :third_started, :third_finished, 'failed')
                """
            ),
            {
                "first_started": now - timedelta(days=1),
                "first_finished": now - timedelta(days=1),
                "second_started": now - timedelta(days=2),
                "second_finished": now - timedelta(days=2),
                "third_started": now - timedelta(days=3),
                "third_finished": now - timedelta(days=3),
            },
        )
        test_session.commit()

        that_app = FastAPI()
        that_app.include_router(router)

        def override_get_session():
            yield test_session

        that_app.dependency_overrides[get_session] = override_get_session

        response = TestClient(that_app).get(
            "/api/cadence/rows-affected-leaderboard",
            params={"days": 30},
        )

        assert response.status_code == 200
        payload = response.json()
        assert len(payload["jobs"]) == 2

        busiest = max(payload["jobs"], key=lambda item: item["total_rows_affected"])
        assert busiest["total_rows_affected"] == 7
        assert busiest["run_count"] == 2

        failing = next(item for item in payload["jobs"] if item["job"] == "beta")
        assert failing["failure_count"] == 1

        print("PASS")