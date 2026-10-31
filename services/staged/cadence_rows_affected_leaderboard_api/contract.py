from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


router = APIRouter(prefix="/api")


def get_rows_affected_leaderboard(
    days: int,
    session: Session,
) -> Dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = session.execute(
        text(
            """
            SELECT
                r.job AS job,
                COUNT(*) AS run_count,
                SUM(CASE WHEN r.status = 'success' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN r.status = 'failed' THEN 1 ELSE 0 END) AS failure_count,
                SUM(r.rows_affected) AS total_rows_affected,
                CAST(SUM(r.rows_affected) AS REAL) / NULLIF(COUNT(*), 0) AS avg_rows_affected,
                MAX(r.started_at) AS last_started_at,
                (
                    SELECT lr.status
                    FROM cadence_job_runs AS lr
                    WHERE lr.job = r.job
                      AND lr.started_at >= :cutoff
                    ORDER BY lr.started_at DESC, lr.id DESC
                    LIMIT 1
                ) AS last_status
            FROM cadence_job_runs AS r
            WHERE r.started_at >= :cutoff
            GROUP BY r.job
            ORDER BY total_rows_affected DESC, r.job ASC
            """
        ),
        {"cutoff": cutoff},
    )

    jobs: List[Dict[str, Any]] = []
    for row in result.mappings():
        last_started_at = row["last_started_at"]
        if isinstance(last_started_at, datetime):
            last_started_at = last_started_at.isoformat()

        jobs.append(
            {
                "job": row["job"],
                "run_count": int(row["run_count"] or 0),
                "success_count": int(row["success_count"] or 0),
                "failure_count": int(row["failure_count"] or 0),
                "total_rows_affected": int(row["total_rows_affected"] or 0),
                "avg_rows_affected": float(row["avg_rows_affected"] or 0.0),
                "last_started_at": last_started_at,
                "last_status": row["last_status"],
            }
        )

    return {
        "days": days,
        "jobs": jobs,
        "job_count": len(jobs),
    }


@router.get("/cadence/rows-affected-leaderboard")
def rows_affected_leaderboard(
    days: int = Query(7, ge=1, le=365),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_rows_affected_leaderboard(days=days, session=session)


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE cadence_job_runs (
                    id INTEGER PRIMARY KEY,
                    job TEXT NOT NULL,
                    rows_affected INTEGER,
                    status TEXT,
                    started_at DATETIME,
                    finished_at DATETIME,
                    detail TEXT
                )
                """
            )
        )
        now = datetime.utcnow()
        connection.execute(
            text(
                """
                INSERT INTO cadence_job_runs
                    (id, job, rows_affected, status, started_at, finished_at, detail)
                VALUES
                    (1, 'alpha', 3, 'success', :started_1, :finished_1, 'first'),
                    (2, 'alpha', 4, 'failed', :started_2, :finished_2, 'second'),
                    (3, 'beta', 2, 'success', :started_3, :finished_3, 'third')
                """
            ),
            {
                "started_1": now - timedelta(hours=3),
                "finished_1": now - timedelta(hours=3, minutes=-1),
                "started_2": now - timedelta(hours=2),
                "finished_2": now - timedelta(hours=2, minutes=-1),
                "started_3": now - timedelta(hours=1),
                "finished_3": now - timedelta(hours=1, minutes=-1),
            },
        )

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/cadence/rows-affected-leaderboard",
            params={"days": 7},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 7
        assert payload["job_count"] == 2
        assert len(payload["jobs"]) == 2

        busiest = payload["jobs"][0]
        assert busiest["job"] == "alpha"
        assert busiest["total_rows_affected"] == 7
        assert busiest["run_count"] == 2
        assert busiest["failure_count"] == 1

    print("PASS")