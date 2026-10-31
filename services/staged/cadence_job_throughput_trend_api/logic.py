from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Optional

from fastapi import Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


_SUCCESS_STATUSES = ("ok", "success", "succeeded", "complete", "completed")
_FAILED_STATUSES = ("failed", "failure", "error")


def _date_key(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _as_datetime(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def get_cadence_job_throughput_trend(
    days: int = 14,
    db: Session = Depends(get_session),
    *,
    session: Optional[Session] = None,
) -> list[dict]:
    if session is not None:
        db = session

    day_count = max(1, int(days))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=day_count)
    day_expr = func.date(CadenceJobRun.started_at)

    aggregate_query = (
        select(
            day_expr.label("run_date"),
            CadenceJobRun.job.label("job"),
            func.count(CadenceJobRun.id).label("runs_total"),
            func.coalesce(
                func.sum(
                    case(
                        (func.lower(CadenceJobRun.status).in_(_SUCCESS_STATUSES), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("runs_ok"),
            func.coalesce(
                func.sum(
                    case(
                        (func.lower(CadenceJobRun.status).in_(_FAILED_STATUSES), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("runs_failed"),
            func.coalesce(func.sum(CadenceJobRun.rows_affected), 0).label(
                "rows_affected"
            ),
        )
        .where(CadenceJobRun.started_at >= cutoff)
        .group_by(day_expr, CadenceJobRun.job)
        .order_by(day_expr, CadenceJobRun.job)
    )

    aggregate_rows = db.execute(aggregate_query).mappings().all()

    duration_query = (
        select(
            day_expr.label("run_date"),
            CadenceJobRun.job.label("job"),
            CadenceJobRun.started_at.label("started_at"),
            CadenceJobRun.finished_at.label("finished_at"),
        )
        .where(CadenceJobRun.started_at >= cutoff)
    )

    durations: dict[tuple[str, str], list[float]] = {}
    for row in db.execute(duration_query).mappings():
        started_at = _as_datetime(row["started_at"])
        finished_at = _as_datetime(row["finished_at"])
        if started_at is None or finished_at is None:
            continue

        duration = max(0.0, (finished_at - started_at).total_seconds())
        key = (_date_key(row["run_date"]), str(row["job"]))
        durations.setdefault(key, []).append(duration)

    series = []
    for row in aggregate_rows:
        date_value = _date_key(row["run_date"])
        job = str(row["job"])
        samples = durations.get((date_value, job), [])
        series.append(
            {
                "date": date_value,
                "job": job,
                "runs_total": int(row["runs_total"] or 0),
                "runs_ok": int(row["runs_ok"] or 0),
                "runs_failed": int(row["runs_failed"] or 0),
                "rows_affected": int(row["rows_affected"] or 0),
                "median_duration_seconds": float(median(samples)) if samples else 0.0,
            }
        )

    return series


def get_throughput_trend(
    days: int = 14,
    db: Session = Depends(get_session),
    *,
    session: Optional[Session] = None,
) -> list[dict]:
    return get_cadence_job_throughput_trend(days=days, db=db, session=session)


def get_cadence_job_throughput(
    days: int = 14,
    db: Session = Depends(get_session),
    *,
    session: Optional[Session] = None,
) -> list[dict]:
    return get_cadence_job_throughput_trend(days=days, db=db, session=session)


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
    CadenceJobRun.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today = now.replace(hour=12, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)

    with TestSession() as test_db:
        test_db.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="ok",
                    started_at=today,
                    finished_at=today + timedelta(seconds=2),
                    rows_affected=10,
                    detail="completed",
                ),
                CadenceJobRun(
                    job="alpha",
                    status="failed",
                    started_at=today + timedelta(hours=1),
                    finished_at=today + timedelta(hours=1, seconds=4),
                    rows_affected=0,
                    detail="failed",
                ),
                CadenceJobRun(
                    job="beta",
                    status="ok",
                    started_at=yesterday,
                    finished_at=yesterday + timedelta(seconds=3),
                    rows_affected=7,
                    detail="completed",
                ),
                CadenceJobRun(
                    job="beta",
                    status="ok",
                    started_at=yesterday + timedelta(hours=1),
                    finished_at=yesterday + timedelta(hours=1, seconds=5),
                    rows_affected=8,
                    detail="completed",
                ),
            ]
        )
        test_db.commit()

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/cadence/jobs/throughput")
    def cadence_job_throughput(
        days: int = 14, db: Session = Depends(get_session)
    ):
        return {
            "days": days,
            "series": get_cadence_job_throughput_trend(days=days, db=db),
        }

    with TestClient(test_app) as client:
        response = client.get(
            "/api/cadence/jobs/throughput", params={"days": 14}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 14
        pair = next(
            item
            for item in payload["series"]
            if item["date"] == today.date().isoformat() and item["job"] == "alpha"
        )
        assert pair["runs_total"] == 2
        assert pair["runs_ok"] == 1
        assert isinstance(pair["median_duration_seconds"], float)
        assert pair["median_duration_seconds"] >= 0.0

    print("PASS")