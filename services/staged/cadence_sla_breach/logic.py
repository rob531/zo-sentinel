from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import bindparam, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun


SLA_SECONDS = 1800
DEFAULT_HOURS = 24


class SlaBreach(BaseModel):
    job: str
    status: str
    duration_seconds: float | None
    started_at: datetime | None
    finished_at: datetime | None
    rows_affected: int | None
    breach_reason: str


class SlaBreachSummary(BaseModel):
    breached_jobs: int
    total_failures: int
    max_duration_seconds: float | None


class SlaBreachResponse(BaseModel):
    hours: int
    breaches: list[SlaBreach]
    summary: SlaBreachSummary


def _duration_seconds(started_at: Any, finished_at: Any) -> float | None:
    if started_at is None or finished_at is None:
        return None
    duration = (finished_at - started_at).total_seconds()
    return max(0.0, duration)


def get_breaches(
    db: Session = Depends(get_session),
    hours: int = DEFAULT_HOURS,
) -> dict[str, Any]:
    if hours < 0:
        raise ValueError("hours must be non-negative")

    started_after = datetime.now(timezone.utc) - timedelta(hours=hours)
    statement = (
        select(
            CadenceJobRun.job,
            CadenceJobRun.status,
            CadenceJobRun.started_at,
            CadenceJobRun.finished_at,
            CadenceJobRun.rows_affected,
        )
        .where(CadenceJobRun.started_at >= bindparam("started_after"))
        .order_by(CadenceJobRun.started_at.asc(), CadenceJobRun.id.asc())
    )

    rows = db.execute(statement, {"started_after": started_after}).all()

    breaches: list[dict[str, Any]] = []
    total_failures = 0
    max_duration: float | None = None

    for row in rows:
        duration = _duration_seconds(row.started_at, row.finished_at)
        if duration is not None:
            max_duration = duration if max_duration is None else max(max_duration, duration)

        if row.status != "success":
            total_failures += 1
            breach_reason = "status_failed"
        elif duration is not None and duration > SLA_SECONDS:
            breach_reason = "sla_exceeded"
        else:
            continue

        breaches.append(
            {
                "job": row.job,
                "status": row.status,
                "duration_seconds": duration,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "rows_affected": row.rows_affected,
                "breach_reason": breach_reason,
            }
        )

    return {
        "hours": hours,
        "breaches": breaches,
        "summary": {
            "breached_jobs": len({item["job"] for item in breaches}),
            "total_failures": total_failures,
            "max_duration_seconds": max_duration,
        },
    }


def get_cadence_sla_breaches(
    db: Session = Depends(get_session),
    hours: int = DEFAULT_HOURS,
) -> dict[str, Any]:
    return get_breaches(db=db, hours=hours)


def get_sla_breaches(
    db: Session = Depends(get_session),
    hours: int = DEFAULT_HOURS,
) -> dict[str, Any]:
    return get_breaches(db=db, hours=hours)


def list_breaches(
    db: Session = Depends(get_session),
    hours: int = DEFAULT_HOURS,
) -> dict[str, Any]:
    return get_breaches(db=db, hours=hours)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add_all(
            [
                CadenceJobRun(
                    job="fast-success",
                    status="success",
                    started_at=now - timedelta(minutes=5),
                    finished_at=now - timedelta(minutes=4, seconds=50),
                    rows_affected=10,
                ),
                CadenceJobRun(
                    job="slow-success",
                    status="success",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=2) + timedelta(seconds=4000),
                    rows_affected=20,
                ),
                CadenceJobRun(
                    job="failed-job",
                    status="failed",
                    started_at=now - timedelta(minutes=10),
                    finished_at=now - timedelta(minutes=9),
                    rows_affected=0,
                ),
                CadenceJobRun(
                    job="old-job",
                    status="success",
                    started_at=now - timedelta(hours=100),
                    finished_at=now - timedelta(hours=100) + timedelta(seconds=10),
                    rows_affected=1,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/cadence/sla/breaches", response_model=SlaBreachResponse)
    def cadence_sla_breaches(
        hours: int = DEFAULT_HOURS,
        db: Session = Depends(get_session),
    ):
        return get_breaches(db=db, hours=hours)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/sla/breaches", params={"hours": 24})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["breaches"]) == 2, payload
        slow = next(
            item for item in payload["breaches"] if item["job"] == "slow-success"
        )
        assert slow["breach_reason"] == "sla_exceeded", payload
        assert all(item["job"] != "old-job" for item in payload["breaches"]), payload

    print("PASS")


if __name__ == "__main__":
    _self_test()