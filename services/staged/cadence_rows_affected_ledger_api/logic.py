from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session  # noqa: F401 - the real application session dependency
from app.models import CadenceJobRun


_SUCCESS_STATUSES = {"ok", "success", "succeeded", "successful", "completed", "complete"}
_FAILURE_STATUSES = {"failed", "failure", "error", "errored"}


def _utc_naive(value: datetime) -> datetime:
    """Return a UTC-naive datetime suitable for SQLite and PostgreSQL comparisons."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _runtime_seconds(started_at: Any, finished_at: Any) -> Optional[float]:
    if not isinstance(started_at, datetime) or not isinstance(finished_at, datetime):
        return None

    started = _utc_naive(started_at)
    finished = _utc_naive(finished_at)
    elapsed = (finished - started).total_seconds()
    return elapsed if elapsed >= 0 else None


def _is_success(status: Any) -> bool:
    return str(status or "").strip().lower() in _SUCCESS_STATUSES


def _is_failure(status: Any) -> bool:
    return str(status or "").strip().lower() in _FAILURE_STATUSES


async def get_rows_affected_statistics(
    session: Session,
    days: int = 7,
    job: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Aggregate recent cadence job runs from the application's cadence_job_runs table."""
    cutoff = _utc_naive(datetime.now(timezone.utc)) - timedelta(days=max(0, days))

    statement = select(
        CadenceJobRun.job,
        CadenceJobRun.status,
        CadenceJobRun.started_at,
        CadenceJobRun.finished_at,
        CadenceJobRun.rows_affected,
        CadenceJobRun.detail,
    ).where(CadenceJobRun.started_at >= cutoff)

    if job is not None:
        statement = statement.where(CadenceJobRun.job == job)

    statement = statement.order_by(CadenceJobRun.started_at.desc())

    result = session.execute(statement)
    if inspect.isawaitable(result):
        result = await result

    grouped: Dict[str, Dict[str, Any]] = {}

    for row in result.all():
        row_job = row.job
        job_key = str(row_job)
        bucket = grouped.setdefault(
            job_key,
            {
                "runs": 0,
                "ok": 0,
                "failed": 0,
                "rows_total": 0,
                "successful_rows": [],
                "runtimes": [],
                "last_runs": [],
            },
        )

        bucket["runs"] += 1
        status = row.status
        rows_affected = row.rows_affected if row.rows_affected is not None else 0

        if _is_success(status):
            bucket["ok"] += 1
            bucket["rows_total"] += int(rows_affected)
            bucket["successful_rows"].append(int(rows_affected))
        elif _is_failure(status):
            bucket["failed"] += 1

        runtime = _runtime_seconds(row.started_at, row.finished_at)
        if runtime is not None:
            bucket["runtimes"].append(runtime)

        bucket["last_runs"].append(
            {
                "started_at": row.started_at,
                "status": status,
                "rows_affected": row.rows_affected,
            }
        )

    output: List[Dict[str, Any]] = []

    for job_name in sorted(grouped):
        bucket = grouped[job_name]
        successful_rows = bucket["successful_rows"]
        runtimes = bucket["runtimes"]
        rows_total = bucket["rows_total"]
        ok = bucket["ok"]

        output.append(
            {
                "job": job_name,
                "runs": bucket["runs"],
                "ok": ok,
                "failed": bucket["failed"],
                "rows_total": rows_total,
                "rows_avg": (rows_total / ok) if ok else 0.0,
                "runtime_avg_s": (sum(runtimes) / len(runtimes)) if runtimes else 0.0,
                "runtime_median_s": float(median(runtimes)) if runtimes else 0.0,
                "last_runs": bucket["last_runs"][:10],
            }
        )

    return output


# Compatibility aliases for routers using the service-oriented function name.
get_cadence_rows_affected = get_rows_affected_statistics
get_cadence_rows_affected_statistics = get_rows_affected_statistics
get_cadence_rows_affected_ledger = get_rows_affected_statistics
get_rows_affected_ledger = get_rows_affected_statistics


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    CadenceJobRun.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with SessionLocal() as test_session:
        test_session.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="success",
                    started_at=now - timedelta(minutes=10),
                    finished_at=now - timedelta(minutes=9),
                    rows_affected=7,
                    detail="completed successfully",
                ),
                CadenceJobRun(
                    job="alpha",
                    status="failed",
                    started_at=now - timedelta(minutes=5),
                    finished_at=now - timedelta(minutes=4),
                    rows_affected=2,
                    detail="test failure",
                ),
                CadenceJobRun(
                    job="beta",
                    status="success",
                    started_at=now - timedelta(minutes=3),
                    finished_at=now - timedelta(minutes=2),
                    rows_affected=3,
                    detail="completed successfully",
                ),
                CadenceJobRun(
                    job="beta",
                    status="failed",
                    started_at=now - timedelta(minutes=1),
                    finished_at=now,
                    rows_affected=0,
                    detail="test failure",
                ),
            ]
        )
        test_session.commit()

        import asyncio

        statistics = asyncio.run(
            get_rows_affected_statistics(test_session, days=30, job="alpha")
        )

        alpha = next(item for item in statistics if item["job"] == "alpha")
        assert alpha["runs"] == 2
        assert alpha["ok"] == 1
        assert alpha["failed"] == 1
        assert alpha["rows_total"] == 7
        assert len(alpha["last_runs"]) == 2

    print("PASS")