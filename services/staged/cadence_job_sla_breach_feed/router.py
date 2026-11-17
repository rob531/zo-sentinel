"""HTTP surface for the cadence job SLA breach feed."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

from .logic import __dict__ as _logic_exports

router = APIRouter(prefix="/api", tags=["cadence"])


class JobSlaBreach(BaseModel):
    job: str
    last_status: str
    last_finished_at: str
    last_duration_seconds: float
    success_rate: float
    breached: bool


class SlaBreachFeedResponse(BaseModel):
    window_runs: int
    jobs: list[JobSlaBreach]


def _sla_breach_logic() -> Any:
    preferred = (
        "list_sla_breaches",
        "get_sla_breaches",
        "sla_breaches",
        "get_sla_breach_feed",
        "sla_breach_feed",
        "sla_breach_summary",
        "get_sla_breach_summary",
    )
    for name in preferred:
        candidate = _logic_exports.get(name)
        if callable(candidate):
            return candidate

    for name, candidate in _logic_exports.items():
        if callable(candidate) and "sla" in name.lower() and "breach" in name.lower():
            return candidate

    raise RuntimeError("The cadence SLA breach logic function is unavailable")


def _normalize_result(result: Any) -> dict[str, Any]:
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    elif hasattr(result, "dict"):
        result = result.dict()

    if isinstance(result, tuple) and len(result) == 2:
        result = {"window_runs": result[0], "jobs": result[1]}

    if not isinstance(result, dict):
        raise TypeError("SLA breach logic must return a mapping")

    jobs: list[dict[str, Any]] = []
    for item in result.get("jobs", []):
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        elif hasattr(item, "dict"):
            item = item.dict()
        else:
            item = dict(item)

        finished_at = item.get("last_finished_at")
        if isinstance(finished_at, (datetime, date)):
            item["last_finished_at"] = finished_at.isoformat()
        elif finished_at is not None:
            item["last_finished_at"] = str(finished_at)

        jobs.append(item)

    return {"window_runs": int(result.get("window_runs", 0)), "jobs": jobs}


@router.get("/cadence/sla-breaches", response_model=SlaBreachFeedResponse)
def get_sla_breaches(
    db: Session = Depends(get_session),
    sla_seconds: int = Query(default=900, ge=0),
) -> SlaBreachFeedResponse:
    payload = _normalize_result(_sla_breach_logic()(db, sla_seconds=sla_seconds))
    return SlaBreachFeedResponse(**payload)


if __name__ == "__main__":
    from datetime import timedelta

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
    CadenceJobRun.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.utcnow()
    with TestSession() as session:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="scan",
                    status="success",
                    started_at=now - timedelta(minutes=30),
                    finished_at=now - timedelta(minutes=29),
                    rows_affected=10,
                    detail="ok",
                ),
                CadenceJobRun(
                    id=2,
                    job="scan",
                    status="success",
                    started_at=now - timedelta(minutes=20),
                    finished_at=now - timedelta(minutes=19),
                    rows_affected=10,
                    detail="ok",
                ),
                CadenceJobRun(
                    id=3,
                    job="scan",
                    status="failed",
                    started_at=now - timedelta(minutes=2),
                    finished_at=now - timedelta(minutes=1),
                    rows_affected=0,
                    detail="failed",
                ),
                CadenceJobRun(
                    id=4,
                    job="threat_intel",
                    status="success",
                    started_at=now - timedelta(minutes=10),
                    finished_at=now - timedelta(minutes=9),
                    rows_affected=5,
                    detail="ok",
                ),
            ]
        )
        session.commit()

    with TestClient(test_app) as client:
        response = client.get("/api/cadence/sla-breaches?sla_seconds=60")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["jobs"]) == 2, payload

        jobs = {item["job"]: item for item in payload["jobs"]}
        assert jobs["scan"]["breached"] is True, payload
        assert round(jobs["scan"]["success_rate"], 2) == 0.67, payload
        assert jobs["threat_intel"]["breached"] is False, payload

    test_engine.dispose()
    print("PASS")