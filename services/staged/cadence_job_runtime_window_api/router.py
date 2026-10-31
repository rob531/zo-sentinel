from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import CadenceJobRun
from .logic import get_runtime_window


class RuntimeWindowJob(BaseModel):
    job: str
    runs: int
    failures: int
    median_seconds: float
    p95_seconds: float
    min_seconds: float
    max_seconds: float


class RuntimeWindowResponse(BaseModel):
    days: int
    jobs: list[RuntimeWindowJob]


router = APIRouter(prefix="/api", tags=["cadence"])


@router.get("/cadence/runtime-window", response_model=RuntimeWindowResponse)
def cadence_runtime_window(
    job: Optional[str] = Query(default=None),
    days: int = Query(default=7, ge=1, le=90),
    session=Depends(get_session),
):
    return get_runtime_window(session, job, days)