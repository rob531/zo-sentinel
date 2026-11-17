from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun


def _normalized_days(days: int | None) -> int:
    if days is None:
        return 7
    try:
        value = int(days)
    except (TypeError, ValueError):
        return 7
    return max(1, min(value, 90))


def _duration_expression(session: Session):
    bind = session.get_bind()
    dialect = getattr(bind, "dialect", None)
    if dialect is not None and dialect.name == "postgresql":
        return func.extract(
            "epoch",
            CadenceJobRun.finished_at - CadenceJobRun.started_at,
        )
    return None


def get_rows_affected_profile(
    days: int = 7,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    window_days = _normalized_days(days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    duration_expression = _duration_expression(session)
    if duration_expression is None:
        statement = select(CadenceJobRun).where(
            CadenceJobRun.started_at >= cutoff
        )
        result = session.execute(statement)
        rows = [(row, None) for row in result.scalars().all()]
    else:
        statement = select(CadenceJobRun, duration_expression).where(
            CadenceJobRun.started_at >= cutoff
        )
        rows = [(row, duration) for row, duration in session.execute(statement).all()]

    grouped: dict[str, dict[str, Any]] = {}

    for run, duration in rows:
        job = str(run.job)
        group = grouped.setdefault(
            job,
            {
                "job": job,
                "runs": 0,
                "ok_runs": 0,
                "failed_runs": 0,
                "total_rows_affected": 0,
                "durations": [],
                "last_run_at": None,
            },
        )

        group["runs"] += 1
        status = str(run.status or "").strip().lower()
        if status == "ok":
            group["ok_runs"] += 1
        elif status == "failed":
            group["failed_runs"] += 1

        group["total_rows_affected"] += int(run.rows_affected or 0)

        if duration is not None:
            group["durations"].append(float(duration))
        elif run.finished_at is not None and run.started_at is not None:
            group["durations"].append(
                (run.finished_at - run.started_at).total_seconds()
            )

        if group["last_run_at"] is None or run.started_at > group["last_run_at"]:
            group["last_run_at"] = run.started_at

    jobs: list[dict[str, Any]] = []
    for group in sorted(grouped.values(), key=lambda item: item["job"]):
        runs = group["runs"]
        durations = group["durations"]
        jobs.append(
            {
                "job": group["job"],
                "runs": runs,
                "ok_runs": group["ok_runs"],
                "failed_runs": group["failed_runs"],
                "total_rows_affected": group["total_rows_affected"],
                "avg_rows_affected": (
                    group["total_rows_affected"] / runs if runs else 0.0
                ),
                "max_rows_affected": max(
                    (
                        int(run.rows_affected or 0)
                        for run, _ in rows
                        if str(run.job) == group["job"]
                    ),
                    default=0,
                ),
                "avg_duration_seconds": (
                    sum(durations) / len(durations) if durations else None
                ),
                "last_run_at": group["last_run_at"],
            }
        )

    return {
        "days": window_days,
        "jobs": jobs,
        "totals": {
            "runs": len(rows),
            "jobs": len(jobs),
            "total_rows_affected": sum(
                int(run.rows_affected or 0) for run, _ in rows
            ),
        },
    }


def rows_affected_profile(
    days: int = 7,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_rows_affected_profile(days=days, session=session)


def get_profile(
    days: int = 7,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_rows_affected_profile(days=days, session=session)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    with SessionLocal() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="alpha",
                    status="ok",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2, minutes=-1),
                    rows_affected=10,
                    detail="completed",
                ),
                CadenceJobRun(
                    job="alpha",
                    status="failed",
                    started_at=now - timedelta(days=1),
                    finished_at=now - timedelta(days=1, minutes=-1),
                    rows_affected=None,
                    detail="failed",
                ),
                CadenceJobRun(
                    job="beta",
                    status="ok",
                    started_at=now - timedelta(days=2),
                    finished_at=now - timedelta(days=2, minutes=-1),
                    rows_affected=20,
                    detail="completed",
                ),
                CadenceJobRun(
                    job="beta",
                    status="failed",
                    started_at=now - timedelta(days=3),
                    finished_at=now - timedelta(days=3, minutes=-1),
                    rows_affected=5,
                    detail="failed",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/cadence/rows-affected-profile")
    def test_endpoint(days: int = 7, session: Session = Depends(get_session)):
        return get_rows_affected_profile(days=days, session=session)

    response = TestClient(test_app).get(
        "/api/cadence/rows-affected-profile",
        params={"days": 7},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["runs"] == 4
    alpha = next(item for item in payload["jobs"] if item["job"] == "alpha")
    assert alpha["total_rows_affected"] == 10
    beta = next(item for item in payload["jobs"] if item["job"] == "beta")
    assert beta["total_rows_affected"] == 25
    print("PASS")