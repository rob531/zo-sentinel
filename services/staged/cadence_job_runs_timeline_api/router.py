from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session

from .logic import get_cadence_runs


router = APIRouter(prefix="/api")


class CadenceRun(BaseModel):
    id: int
    job: str
    status: str
    started_at: str
    finished_at: Optional[str] = None
    rows_affected: Optional[int] = None
    duration_seconds: Optional[float] = None


class CadenceSummary(BaseModel):
    job: Optional[str] = None
    total: int
    by_status: Dict[str, int]
    success_rate: float
    avg_rows_affected: Optional[float] = None
    avg_duration_seconds: Optional[float] = None


class CadenceFleetEntry(BaseModel):
    job: str
    runs: int
    success_rate: float
    max_duration_seconds: Optional[float] = None
    median_duration_seconds: Optional[float] = None


class CadenceRunsResponse(BaseModel):
    job: Optional[str] = None
    limit: int
    runs: List[CadenceRun]
    summary: CadenceSummary
    fleet: List[CadenceFleetEntry]


@router.get("/cadence/runs", response_model=CadenceRunsResponse)
def cadence_runs(
    job: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    session=Depends(get_session),
):
    return get_cadence_runs(session, job=job, limit=limit)


if __name__ == "__main__":
    payload = CadenceRunsResponse(
        job=None,
        limit=100,
        runs=[],
        summary=CadenceSummary(
            job=None,
            total=0,
            by_status={},
            success_rate=0.0,
            avg_rows_affected=None,
            avg_duration_seconds=None,
        ),
        fleet=[],
    )
    assert payload.limit == 100
    assert payload.runs == []
    print("PASS")