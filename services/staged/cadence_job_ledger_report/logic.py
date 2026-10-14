"""Cadence job ledger report service."""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Annotated

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import Integer, case, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


class JobLedgerEntry(BaseModel):
    """Single job's ledger summary."""
    job: str
    total_runs: int
    success_count: int
    failure_count: int
    avg_rows_affected: float | None
    last_run_at: datetime | None
    last_status: str | None


class CadenceJobLedgerResponse(BaseModel):
    """Full ledger response."""
    jobs: list[JobLedgerEntry]


def get_cadence_job_ledger(db: Session) -> CadenceJobLedgerResponse:
    """Compute a ledger of cadence job runs."""
    stmt = (
        select(
            CadenceJobRun.job,
            func.count(CadenceJobRun.id).label("total_runs"),
            func.sum(
                case(
                    (CadenceJobRun.status == "success", 1),
                    else_=0,
                )
            ).label("success_count"),
            func.sum(
                case(
                    (CadenceJobRun.status == "failure", 1),
                    else_=0,
                )
            ).label("failure_count"),
            func.avg(CadenceJobRun.rows_affected).label("avg_rows_affected"),
            func.max(CadenceJobRun.started_at).label("last_run_at"),
        )
        .group_by(CadenceJobRun.job)
        .order_by(CadenceJobRun.job)
    )
    results = db.execute(stmt).all()

    jobs = []
    for row in results:
        last_run_stmt = (
            select(CadenceJobRun.status)
            .where(CadenceJobRun.job == row.job)
            .order_by(CadenceJobRun.started_at.desc())
            .limit(1)
        )
        last_result = db.execute(last_run_stmt).scalar()
        jobs.append(
            JobLedgerEntry(
                job=row.job,
                total_runs=row.total_runs,
                success_count=row.success_count or 0,
                failure_count=row.failure_count or 0,
                avg_rows_affected=float(row.avg_rows_affected) if row.avg_rows_affected is not None else None,
                last_run_at=row.last_run_at,
                last_status=last_result,
            )
        )

    return CadenceJobLedgerResponse(jobs=jobs)


def get_ledger(
    db: Annotated[Session, Depends(get_session)],
) -> CadenceJobLedgerResponse:
    """Endpoint to fetch the cadence job ledger."""
    return get_cadence_job_ledger(db)


if __name__ == "__main__":
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    with TestingSessionLocal() as session:
        now = datetime.utcnow()
        session.add_all([
            CadenceJobRun(
                job="batch_score_import",
                status="success",
                started_at=now,
                finished_at=now,
                rows_affected=150,
            ),
            CadenceJobRun(
                job="batch_score_import",
                status="success",
                started_at=now,
                finished_at=now,
                rows_affected=200,
            ),
            CadenceJobRun(
                job="batch_score_import",
                status="failure",
                started_at=now,
                finished_at=now,
                rows_affected=50,
            ),
            CadenceJobRun(
                job="risk_tier_update",
                status="success",
                started_at=now,
                finished_at=now,
                rows_affected=75,
            ),
            CadenceJobRun(
                job="risk_tier_update",
                status="failure",
                started_at=now,
                finished_at=now,
                rows_affected=0,
            ),
            CadenceJobRun(
                job="signal_aggregation",
                status="success",
                started_at=now,
                finished_at=now,
                rows_affected=300,
            ),
            CadenceJobRun(
                job="signal_aggregation",
                status="success",
                started_at=now,
                finished_at=now,
                rows_affected=325,
            ),
            CadenceJobRun(
                job="signal_aggregation",
                status="success",
                started_at=now,
                finished_at=now,
                rows_affected=280,
            ),
        ])
        session.commit()

    with TestingSessionLocal() as session:
        response = get_cadence_job_ledger(session)
        assert response.jobs is not None
        assert len(response.jobs) >= 2, f"Expected >= 2 jobs, got {len(response.jobs)}"
        print("200")
        print(f"job_count={len(response.jobs)}")
        print("PASS")