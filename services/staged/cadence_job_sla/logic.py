import datetime
from collections import defaultdict
from typing import List, Dict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun, Base

router = APIRouter()


_SLA_THRESHOLDS = {
    "scanner": 3600.0,
    "analyser": 1800.0,
    "synthesiser": 900.0,
}
_DEFAULT_SLA = 7200.0


def _sla_threshold(job_name: str) -> float:
    return _SLA_THRESHOLDS.get(job_name.lower(), _DEFAULT_SLA)


def _aggregate_runs(
    rows: List[CadenceJobRun],
) -> List[Dict]:
    """Aggregate CadenceJobRun rows into SLA metrics per job."""
    agg = defaultdict(
        lambda: {
            "job": None,
            "total_runs": 0,
            "succeeded": 0,
            "failed": 0,
            "avg_duration_s": 0.0,
            "sla_threshold_s": 0.0,
            "sla_met": False,
            "sla_pct": 0.0,
        }
    )

    duration_sums = defaultdict(float)
    sla_met_counts = defaultdict(int)

    for row in rows:
        job = row.job
        d = agg[job]
        d["job"] = job
        d["total_runs"] += 1
        if row.status == "succeeded":
            d["succeeded"] += 1
        elif row.status == "failed":
            d["failed"] += 1

        # duration in seconds; guard against nulls
        if row.started_at and row.finished_at:
            dur = (row.finished_at - row.started_at).total_seconds()
        else:
            dur = 0.0
        duration_sums[job] += dur

        threshold = _sla_threshold(job)
        d["sla_threshold_s"] = threshold
        if dur <= threshold:
            sla_met_counts[job] += 1

    result = []
    for job, data in agg.items():
        total = data["total_runs"]
        data["avg_duration_s"] = round(duration_sums[job] / total, 3) if total else 0.0
        data["sla_pct"] = round((sla_met_counts[job] / total) * 100, 2) if total else 0.0
        data["sla_met"] = data["sla_pct"] == 100.0
        result.append(data)

    return result


def _fetch_runs(session: Session, days: int) -> List[CadenceJobRun]:
    """Fetch CadenceJobRun rows from the last ``days`` days."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    stmt = select(CadenceJobRun).where(CadenceJobRun.started_at >= cutoff)
    return session.execute(stmt).scalars().all()


@router.get("/api/cadence/sla")
def get_cadence_sla(
    days: int = Query(7, ge=1, le=30),
    session: Session = Depends(get_session),
) -> List[Dict]:
    """Return SLA metrics for cadence jobs over the past ``days`` days."""
    rows = _fetch_runs(session, days)
    return _aggregate_runs(rows)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (mirrors the real app models)
    # ------------------------------------------------------------------- #
    ENGINE = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(ENGINE)
    SessionLocal = sessionmaker(bind=ENGINE)

    # ------------------------------------------------------------------- #
    # Helper to override the FastAPI dependency during the test
    # ------------------------------------------------------------------- #
    def _override_get_session() -> Session:
        return SessionLocal()

    # ------------------------------------------------------------------- #
    # Seed deterministic data for four jobs across three days
    # ------------------------------------------------------------------- #
    now = datetime.datetime.utcnow()
    session = SessionLocal()

    jobs = ["scanner", "analyser", "synthesiser", "other"]
    runs_per_job = 50
    # For scanner we craft a known SLA ratio: 30 runs meet SLA, 20 exceed, 5 fail
    scanner_success_meet = 30
    scanner_success_exceed = 15
    scanner_failed = 5

    for job in jobs:
        for i in range(runs_per_job):
            # Distribute timestamps over the last 3 days
            started = now - datetime.timedelta(
                days= (i % 3),
                seconds= i * 10,
            )
            # Determine status and duration based on job type
            if job == "scanner":
                if i < scanner_success_meet:
                    status = "succeeded"
                    dur = _sla_threshold(job) - 10  # within SLA
                elif i < scanner_success_meet + scanner_success_exceed:
                    status = "succeeded"
                    dur = _sla_threshold(job) + 100  # exceeds SLA
                else:
                    status = "failed"
                    dur = _sla_threshold(job) + 200  # failed runs still have duration
            else:
                # For other jobs use a simple pattern: half succeed within SLA, half exceed
                if i % 2 == 0:
                    status = "succeeded"
                    dur = _sla_threshold(job) - 5
                else:
                    status = "succeeded"
                    dur = _sla_threshold(job) + 50

            finished = started + datetime.timedelta(seconds=dur)
            run = CadenceJobRun(
                job=job,
                status=status,
                started_at=started,
                finished_at=finished,
                detail={},  # placeholder JSON field
                rows_affected=0,
                id=None,
            )
            session.add(run)
    session.commit()

    # ------------------------------------------------------------------- #
    # Run the aggregation logic
    # ------------------------------------------------------------------- #
    rows = _fetch_runs(session, days=3)
    metrics = _aggregate_runs(rows)

    # ------------------------------------------------------------------- #
    # Assertions for the scanner job
    # ------------------------------------------------------------------- #
    scanner_metric = next(m for m in metrics if m["job"] == "scanner")
    expected_sla_pct = round((scanner_success_meet / runs_per_job) * 100, 2)
    assert scanner_metric["sla_pct"] == expected_sla_pct, "SLA % mismatch"
    assert scanner_metric["failed"] == scanner_failed, "Failed count mismatch"

    print("PASS")