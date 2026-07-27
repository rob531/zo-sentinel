import datetime
from typing import List

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import CadenceJobRun  # type: ignore


class JobReport(BaseModel):
    job: str
    run_count_7d: int = Field(..., alias="run_count_7d")
    success_rate: float
    p50_ms: float | None
    p95_ms: float | None
    sla_violated: bool
    last_run: datetime.datetime | None


class CadenceJobSLAReportResponse(BaseModel):
    jobs: List[JobReport]


def get_cadence_job_sla_report(session: Session = Depends(get_session)) -> CadenceJobSLAReportResponse:
    """Return SLA report for Cadence jobs over the last 7 days."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=7)

    # runtime in milliseconds
    runtime_ms = (
        func.extract("epoch", CadenceJobRun.finished_at - CadenceJobRun.started_at) * 1000
    ).label("runtime_ms")

    # threshold based on job name
    threshold_ms = case(
        [(func.lower(CadenceJobRun.job).like("%heartbeat%"), 300_000)], else_=1_800_000
    )

    # SLA violation condition
    sla_cond = (CadenceJobRun.status == "failed") | (runtime_ms > threshold_ms)
    sla_flag = case([(sla_cond, 1)], else_=0).label("sla_flag")

    # success count
    success_cnt = func.sum(
        case([(CadenceJobRun.status == "success", 1)], else_=0)
    ).label("success_cnt")

    stmt = (
        select(
            CadenceJobRun.job.label("job"),
            func.count().label("run_count"),
            success_cnt,
            func.percentile_cont(0.5).within_group(runtime_ms).label("p50_ms"),
            func.percentile_cont(0.95).within_group(runtime_ms).label("p95_ms"),
            func.max(CadenceJobRun.finished_at).label("last_run"),
            func.max(sla_flag).label("sla_violated"),
        )
        .where(CadenceJobRun.started_at >= cutoff)
        .group_by(CadenceJobRun.job)
    )

    rows = session.execute(stmt).all()

    jobs: List[JobReport] = []
    for r in rows:
        run_count = r.run_count
        success_rate = (r.success_cnt or 0) / run_count if run_count else 0.0
        jobs.append(
            JobReport(
                job=r.job,
                run_count_7d=run_count,
                success_rate=success_rate,
                p50_ms=float(r.p50_ms) if r.p50_ms is not None else None,
                p95_ms=float(r.p95_ms) if r.p95_ms is not None else None,
                sla_violated=bool(r.sla_violated),
                last_run=r.last_run,
            )
        )

    return CadenceJobSLAReportResponse(jobs=jobs)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for testing
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    # Create tables
    Base.metadata.create_all(engine)

    # Helper to add a run
    def add_run(
        sess: Session,
        job: str,
        status: str,
        start: datetime.datetime,
        finish: datetime.datetime,
    ):
        sess.add(
            CadenceJobRun(
                job=job,
                status=status,
                started_at=start,
                finished_at=finish,
                rows_affected=0,
                detail={},
            )
        )

    now = datetime.datetime.utcnow()
    with SessionLocal() as s:
        # heartbeat_job – fast runs (no SLA breach)
        add_run(s, "heartbeat_job", "success", now - datetime.timedelta(hours=1), now - datetime.timedelta(hours=1) + datetime.timedelta(seconds=100))
        add_run(s, "heartbeat_job", "success", now - datetime.timedelta(hours=2), now - datetime.timedelta(hours=2) + datetime.timedelta(seconds=120))

        # scanner_job – one run exceeds 30 min threshold (SLA breach)
        add_run(s, "scanner_job", "success", now - datetime.timedelta(hours=3), now - datetime.timedelta(hours=3) + datetime.timedelta(seconds=1900))

        # good_job_a – normal runs
        add_run(s, "good_job_a", "success", now - datetime.timedelta(hours=4), now - datetime.timedelta(hours=4) + datetime.timedelta(seconds=80))

        # good_job_b – normal runs
        add_run(s, "good_job_b", "success", now - datetime.timedelta(hours=5), now - datetime.timedelta(hours=5) + datetime.timedelta(seconds=90))

        s.commit()

    # Override dependency for test
    def get_test_session() -> Session:
        return SessionLocal()

    # Call the logic directly
    report = get_cadence_job_sla_report(session=get_test_session())

    # Assertions per acceptance criteria
    assert isinstance(report, CadenceJobSLAReportResponse)
    sla_violated_jobs = [j for j in report.jobs if j.sla_violated]
    assert len(sla_violated_jobs) == 1, f"expected 1 SLA‑violated job, got {len(sla_violated_jobs)}"
    for j in report.jobs:
        assert isinstance(j.p50_ms, (float, type(None))), "p50_ms must be a number or None"
    print("PASS")