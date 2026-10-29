from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence

from fastapi import Depends

from app.db import get_session
from app.models import CadenceJobRun


_MAX_DAYS = 90
_DEFAULT_DAYS = 14
_SUCCESS_STATUSES = {"ok", "success", "succeeded", "successful", "complete", "completed"}


def _normalise_days(days: int) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = _DEFAULT_DAYS
    return max(1, min(value, _MAX_DAYS))


def _as_utc_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _duration_seconds(started_at: Any, finished_at: Any) -> float:
    started = _as_utc_datetime(started_at)
    finished = _as_utc_datetime(finished_at)
    if started is None or finished is None:
        return 0.0
    return max(0.0, (finished - started).total_seconds())


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _is_success(status: Any) -> bool:
    if status is None:
        return False
    return str(status).strip().lower() in _SUCCESS_STATUSES


def _day_bounds(now: datetime, days: int) -> tuple[datetime, List[date]]:
    current = now.astimezone(timezone.utc).date()
    first_day = current - timedelta(days=days - 1)
    cutoff = datetime.combine(first_day, datetime.min.time(), tzinfo=timezone.utc)
    all_days = [first_day + timedelta(days=offset) for offset in range(days)]
    return cutoff, all_days


def _build_daily_series(
    observations: Iterable[tuple[date, bool]],
    allowed_days: Sequence[date],
) -> List[Dict[str, Any]]:
    counts: Dict[date, Dict[str, int]] = {
        day: {"date": day.isoformat(), "runs": 0, "errors": 0}
        for day in allowed_days
    }
    for day, is_success in observations:
        bucket = counts.get(day)
        if bucket is None:
            continue
        bucket["runs"] += 1
        if not is_success:
            bucket["errors"] += 1
    return list(counts.values())


def get_slo_burndown(
    days: int = _DEFAULT_DAYS,
    session=Depends(get_session),
) -> Dict[str, Any]:
    normalised_days = _normalise_days(days)
    now = datetime.now(timezone.utc)
    cutoff, allowed_days = _day_bounds(now, normalised_days)

    statement = (
        session.query(CadenceJobRun)
        .filter(CadenceJobRun.started_at >= cutoff)
        .order_by(CadenceJobRun.started_at.asc(), CadenceJobRun.id.asc())
    )
    runs = statement.all()

    grouped: Dict[str, Dict[str, Any]] = {}
    for run in runs:
        job = str(run.job)
        started = _as_utc_datetime(run.started_at)
        if started is None:
            continue
        day = started.date()
        success = _is_success(run.status)
        duration = _duration_seconds(run.started_at, run.finished_at)

        group = grouped.setdefault(
            job,
            {
                "durations": [],
                "daily": [],
            },
        )
        group["durations"].append(duration)
        group["daily"].append((day, success))

    jobs: List[Dict[str, Any]] = []
    for job in sorted(grouped):
        group = grouped[job]
        durations = group["durations"]
        total_runs = len(durations)
        ok_runs = sum(1 for _, successful in group["daily"] if successful)
        error_runs = total_runs - ok_runs
        success_pct = (ok_runs / total_runs * 100.0) if total_runs else 0.0

        jobs.append(
            {
                "job": job,
                "total_runs": total_runs,
                "ok_runs": ok_runs,
                "error_runs": error_runs,
                "success_pct": success_pct,
                "median_seconds": float(median(durations)) if durations else 0.0,
                "p90_seconds": _percentile(durations, 0.90),
                "daily": _build_daily_series(group["daily"], allowed_days),
            }
        )

    worst_jobs = [
        item["job"]
        for item in sorted(jobs, key=lambda item: (item["success_pct"], item["job"]))[:10]
    ]

    return {
        "generated_at": now.isoformat(),
        "days": normalised_days,
        "jobs": jobs,
        "worst_jobs": worst_jobs,
    }


get_cadence_slo = get_slo_burndown
compute_slo_burndown = get_slo_burndown
build_slo_burndown = get_slo_burndown


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(test_engine)

    base = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(test_engine) as test_session:
        for index in range(4):
            started = base - timedelta(hours=index)
            test_session.add(
                CadenceJobRun(
                    job="a",
                    status="ok",
                    started_at=started,
                    finished_at=started + timedelta(seconds=10 + index),
                    rows_affected=1,
                    detail={},
                )
            )
        failed_start = base - timedelta(days=1)
        test_session.add(
            CadenceJobRun(
                job="a",
                status="error",
                started_at=failed_start,
                finished_at=failed_start + timedelta(seconds=20),
                rows_affected=0,
                detail={},
            )
        )
        for index in range(2):
            started = base - timedelta(days=1, hours=index)
            test_session.add(
                CadenceJobRun(
                    job="b",
                    status="ok",
                    started_at=started,
                    finished_at=started + timedelta(seconds=5),
                    rows_affected=1,
                    detail={},
                )
            )
        test_session.commit()

    def override_session():
        with Session(test_engine) as test_session:
            yield test_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_session

    @that_app.get("/api/cadence/slo")
    def cadence_slo(days: int = 14, db=Depends(get_session)):
        return get_slo_burndown(days=days, session=db)

    with TestClient(that_app) as client:
        response = client.get("/api/cadence/slo", params={"days": 14})
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["jobs"]) == 2
        jobs = {item["job"]: item for item in payload["jobs"]}
        assert jobs["a"]["success_pct"] == 80.0
        assert jobs["a"]["ok_runs"] == 4
        assert isinstance(jobs["a"]["median_seconds"], (int, float))
        assert isinstance(jobs["a"]["p90_seconds"], (int, float))
        assert jobs["a"]["median_seconds"] >= 0
        assert jobs["a"]["p90_seconds"] >= 0
        assert payload["worst_jobs"][0] == "a"

    print("PASS")