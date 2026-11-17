"""Data and computation layer for the cadence rows-processed leaderboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


_SUCCESS_STATUSES = ("ok", "success", "succeeded", "complete", "completed")
_FAILED_STATUSES = ("error", "failed", "failure")


def get_rows_processed_leaderboard(db: Session, days: int = 7) -> Dict[str, Any]:
    """Return the rows-processed leaderboard for the requested time window."""
    days = int(days)
    if days < 1:
        raise ValueError("days must be at least 1")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = db.execute(
        select(
            CadenceJobRun.job,
            func.coalesce(func.sum(CadenceJobRun.rows_affected), 0).label(
                "total_rows_affected"
            ),
            func.sum(
                case(
                    (func.lower(CadenceJobRun.status).in_(_SUCCESS_STATUSES), 1),
                    else_=0,
                )
            ).label("success_runs"),
            func.sum(
                case(
                    (func.lower(CadenceJobRun.status).in_(_FAILED_STATUSES), 1),
                    else_=0,
                )
            ).label("failed_runs"),
            func.max(CadenceJobRun.started_at).label("last_run_at"),
        )
        .where(CadenceJobRun.started_at >= cutoff)
        .group_by(CadenceJobRun.job)
    ).all()

    aggregated = [
        {
            "job": job,
            "total_rows_affected": int(total_rows_affected or 0),
            "success_runs": int(success_runs or 0),
            "failed_runs": int(failed_runs or 0),
            "last_run_at": last_run_at.isoformat() if last_run_at else None,
        }
        for job, total_rows_affected, success_runs, failed_runs, last_run_at in rows
    ]

    aggregated.sort(
        key=lambda item: (-item["total_rows_affected"], item["job"])
    )
    top_jobs = aggregated[:20]
    grand_total = sum(item["total_rows_affected"] for item in aggregated)

    return {
        "days": days,
        "jobs": top_jobs,
        "grand_total_rows_affected": grand_total,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# Compatibility aliases for callers using the shorter service-specific names.
get_leaderboard = get_rows_processed_leaderboard
get_cadence_rows_processed_leaderboard = get_rows_processed_leaderboard
rows_processed_leaderboard = get_rows_processed_leaderboard


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="ok",
                    started_at=now - timedelta(hours=1),
                    finished_at=now - timedelta(minutes=59),
                    rows_affected=10,
                    detail="first",
                ),
                CadenceJobRun(
                    job="alpha",
                    status="error",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2, minutes=-1),
                    rows_affected=20,
                    detail="second",
                ),
                CadenceJobRun(
                    job="alpha",
                    status="ok",
                    started_at=now - timedelta(hours=3),
                    finished_at=now - timedelta(hours=3, minutes=-1),
                    rows_affected=30,
                    detail="third",
                ),
                CadenceJobRun(
                    job="beta",
                    status="ok",
                    started_at=now - timedelta(hours=4),
                    finished_at=now - timedelta(hours=4, minutes=-1),
                    rows_affected=40,
                    detail="fourth",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/cadence/rows-processed-leaderboard")
    def leaderboard(days: int = 30, db: Session = Depends(get_session)):
        return get_rows_processed_leaderboard(db, days)

    with TestClient(test_app) as client:
        response = client.get(
            "/api/cadence/rows-processed-leaderboard", params={"days": 30}
        )
        assert response.status_code == 200
        payload = response.json()
        assert {job["job"] for job in payload["jobs"]} == {"alpha", "beta"}
        assert payload["grand_total_rows_affected"] == 100
        alpha = next(job for job in payload["jobs"] if job["job"] == "alpha")
        assert alpha["total_rows_affected"] == 60
        assert alpha["success_runs"] == 2
        assert alpha["failed_runs"] == 1

    print("PASS")