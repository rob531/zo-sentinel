"""Logic for reporting cadence rows written by job."""

from __future__ import annotations

from datetime import date, datetime
from typing import Dict, Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _iso8601(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def rows_written_by_job(db: Session, job: Optional[str] = None) -> Dict[str, object]:
    """Aggregate cadence job runs by job name."""
    successful = case((CadenceJobRun.status == "ok", 1), else_=0)
    failed = case((CadenceJobRun.status == "failed", 1), else_=0)

    query = (
        select(
            CadenceJobRun.job,
            func.count(CadenceJobRun.id).label("runs"),
            func.sum(successful).label("ok"),
            func.sum(failed).label("failed"),
            func.coalesce(func.sum(CadenceJobRun.rows_affected), 0).label("rows_total"),
            func.avg(
                case(
                    (CadenceJobRun.status == "ok", CadenceJobRun.rows_affected),
                    else_=None,
                )
            ).label("rows_avg"),
            func.max(CadenceJobRun.rows_affected).label("rows_max"),
            func.max(CadenceJobRun.started_at).label("last_run_at"),
        )
        .group_by(CadenceJobRun.job)
        .order_by(CadenceJobRun.job.asc())
    )
    if job is not None:
        query = query.where(CadenceJobRun.job == job)

    rows = db.execute(query).all()
    jobs = []
    for row in rows:
        average = row.rows_avg
        jobs.append(
            {
                "job": str(row.job),
                "runs": int(row.runs or 0),
                "ok": int(row.ok or 0),
                "failed": int(row.failed or 0),
                "rows_total": int(row.rows_total or 0),
                "rows_avg": round(float(average), 2) if average is not None else 0.0,
                "rows_max": int(row.rows_max or 0),
                "last_run_at": _iso8601(row.last_run_at),
            }
        )

    return {"jobs": jobs}


def get_rows_written_by_job(db: Session, job: Optional[str] = None) -> Dict[str, object]:
    return rows_written_by_job(db, job)


def cadence_rows_written_by_job(db: Session, job: Optional[str] = None) -> Dict[str, object]:
    return rows_written_by_job(db, job)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)

    seeded_rows = [
        (1, "alpha", "ok", datetime(2024, 1, 1, 10, 0), 10),
        (2, "alpha", "failed", datetime(2024, 1, 2, 10, 0), 20),
        (3, "alpha", "ok", datetime(2024, 1, 3, 10, 0), 30),
        (4, "beta", "ok", datetime(2024, 1, 4, 10, 0), 4),
        (5, "beta", "failed", datetime(2024, 1, 5, 10, 0), 6),
        (6, "gamma", "ok", datetime(2024, 1, 6, 10, 0), 2),
    ]

    with session_factory() as session:
        for identifier, job_name, status, started_at, rows_affected in seeded_rows:
            session.add(
                CadenceJobRun(
                    id=identifier,
                    job=job_name,
                    status=status,
                    started_at=started_at,
                    rows_affected=rows_affected,
                )
            )
        session.commit()

    that_app = FastAPI()

    @that_app.get("/api/cadence/rows-written")
    def cadence_endpoint(db: Session = Depends(get_session)):
        return rows_written_by_job(db)

    def override_session():
        with session_factory() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_session
    response = TestClient(that_app).get("/api/cadence/rows-written")

    assert response.status_code == 200
    payload = response.json()
    alpha = next(item for item in payload["jobs"] if item["job"] == "alpha")
    assert alpha["runs"] == 3
    assert alpha["ok"] + alpha["failed"] == 3
    assert sum(item["rows_total"] for item in payload["jobs"]) == sum(
        row[4] for row in seeded_rows
    )
    print("PASS")