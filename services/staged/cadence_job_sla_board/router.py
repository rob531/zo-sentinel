"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are relative so the module works identically at
services.staged.<name> and services.active.<name>.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import CadenceJobRun

from . import logic as _logic
from .logic import *  # noqa: F401,F403


router = APIRouter(prefix="/api/cadence/jobs", tags=["cadence"])


class DailyPoint(BaseModel):
    date: date
    runs: int
    failures: int


class JobSla(BaseModel):
    job: str
    runs: int
    success_count: int
    failure_count: int
    success_rate_pct: float
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    seconds_since_last_run: float
    avg_duration_seconds: float
    max_duration_seconds: float
    total_rows_affected: int
    daily: list[DailyPoint]


class SlaTotals(BaseModel):
    jobs: int
    runs: int
    failures: int


class SlaBoardResponse(BaseModel):
    days: int
    generated_at: datetime
    jobs: list[JobSla]
    stale_jobs: list[str]
    totals: SlaTotals


def _run_sla_logic(db: Session, days: int) -> dict[str, Any]:
    """Call the service logic while accommodating its semantic aliases."""
    candidates = (
        "cadence_sla_board",
        "get_cadence_sla_board",
        "build_cadence_sla_board",
        "sla_board",
        "get_sla_board",
        "build_sla_board",
    )
    for name in candidates:
        function = globals().get(name)
        if function is None:
            function = getattr(_logic, name, None)
        if callable(function):
            result = function(db, days)
            if hasattr(result, "model_dump"):
                return result.model_dump()
            if hasattr(result, "dict"):
                return result.dict()
            return dict(result)

    raise RuntimeError("No cadence SLA board function is available in logic.py")


@router.get("/sla", response_model=SlaBoardResponse)
def get_sla_board(
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_session),
) -> SlaBoardResponse:
    payload = _run_sla_logic(db, days)
    payload.setdefault("days", days)
    payload.setdefault("generated_at", datetime.now(timezone.utc))
    payload.setdefault("jobs", [])
    payload.setdefault("stale_jobs", [])
    payload.setdefault("totals", {"jobs": 0, "runs": 0, "failures": 0})
    return SlaBoardResponse(**payload)


def _self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CadenceJobRun.__table__.create(engine)
    local_session = sessionmaker(bind=engine, expire_on_commit=False)()
    now = datetime.now(timezone.utc)

    local_session.add_all(
        [
            CadenceJobRun(
                job="scoring_wave",
                status="success",
                started_at=now - timedelta(hours=3),
                finished_at=now - timedelta(hours=3) + timedelta(seconds=10),
                rows_affected=10,
                detail={},
            ),
            CadenceJobRun(
                job="scoring_wave",
                status="success",
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=2) + timedelta(seconds=20),
                rows_affected=5,
                detail={},
            ),
            CadenceJobRun(
                job="scoring_wave",
                status="failure",
                started_at=now - timedelta(hours=1),
                finished_at=now - timedelta(hours=1) + timedelta(seconds=5),
                rows_affected=0,
                detail={},
            ),
            CadenceJobRun(
                job="advisory_fetch",
                status="success",
                started_at=now - timedelta(minutes=30),
                finished_at=now - timedelta(minutes=29),
                rows_affected=1,
                detail={},
            ),
        ]
    )
    local_session.commit()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: local_session

    with TestClient(that_app) as client:
        response = client.get("/api/cadence/jobs/sla", params={"days": 7})
        assert response.status_code == 200, response.text
        body = response.json()

    assert len(body["jobs"]) == 2, body
    scoring = next(item for item in body["jobs"] if item["job"] == "scoring_wave")
    assert scoring["runs"] == 3, scoring
    assert scoring["success_count"] == 2, scoring
    assert scoring["failure_count"] == 1, scoring
    assert scoring["success_rate_pct"] == 66.67, scoring
    assert scoring["total_rows_affected"] == 15, scoring
    assert len(scoring["daily"]) >= 1, scoring
    assert body["totals"]["failures"] == 1, body

    local_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()