from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

from .logic import get_rows_affected_trend


router = APIRouter(prefix="/api")


class RowsAffectedSeriesPoint(BaseModel):
    date: str
    total_rows_affected: int
    run_count: int
    success_count: int
    failed_count: int
    avg_duration_seconds: Optional[float]


class RowsAffectedJobSummary(BaseModel):
    job: str
    run_count: int
    total_rows_affected: int
    last_status: str


class RowsAffectedTrendResponse(BaseModel):
    days: int
    series: list[RowsAffectedSeriesPoint]
    jobs: list[RowsAffectedJobSummary]
    generated_at: datetime


@router.get(
    "/cadence/jobs/rows-affected",
    response_model=RowsAffectedTrendResponse,
)
def rows_affected_trend(
    job: Optional[str] = Query(default=None),
    days: int = Query(default=14, ge=1, le=365),
    session: Session = Depends(get_session),
) -> RowsAffectedTrendResponse:
    return get_rows_affected_trend(session=session, job=job, days=days)


def _run_self_test() -> None:
    from fastapi import FastAPI

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: object()

    original_logic = globals()["get_rows_affected_trend"]

    def _test_logic(**_kwargs):
        return {
            "days": 14,
            "series": [
                {
                    "date": "2025-01-01",
                    "total_rows_affected": 10,
                    "run_count": 1,
                    "success_count": 0,
                    "failed_count": 1,
                    "avg_duration_seconds": None,
                },
                {
                    "date": "2025-01-02",
                    "total_rows_affected": 20,
                    "run_count": 1,
                    "success_count": 1,
                    "failed_count": 0,
                    "avg_duration_seconds": 2.5,
                },
            ],
            "jobs": [
                {
                    "job": "example",
                    "run_count": 2,
                    "total_rows_affected": 30,
                    "last_status": "success",
                }
            ],
            "generated_at": datetime.now(timezone.utc),
        }

    globals()["get_rows_affected_trend"] = _test_logic
    try:
        response = TestClient(that_app).get(
            "/api/cadence/jobs/rows-affected",
            params={"days": 14},
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["series"]) == 2
        assert payload["series"][0]["failed_count"] == 1
        assert payload["series"][0]["avg_duration_seconds"] is None
    finally:
        globals()["get_rows_affected_trend"] = original_logic

    print("PASS")


if __name__ == "__main__":
    _run_self_test()