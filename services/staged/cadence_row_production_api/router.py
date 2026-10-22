from typing import Dict, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

from .logic import get_cadence_production


class CadenceJobProduction(BaseModel):
    job: str
    runs: int
    succeeded: int
    failed: int
    statuses: Dict[str, int] = Field(default_factory=dict)
    total_rows_affected: int
    mean_rows_affected: float
    slowest_run_seconds: float
    last_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None


class CadenceProductionTotals(BaseModel):
    runs: int
    succeeded: int
    failed: int
    rows_affected: int


class CadenceProductionResponse(BaseModel):
    days: int
    jobs: list[CadenceJobProduction] = Field(default_factory=list)
    totals: CadenceProductionTotals


router = APIRouter(prefix="/api", tags=["cadence"])


@router.get(
    "/cadence/production",
    response_model=CadenceProductionResponse,
)
def cadence_production(
    days: int = Query(14, ge=1, le=3650),
    job: Optional[str] = Query(None),
    session: Session = Depends(get_session),
) -> CadenceProductionResponse:
    result = get_cadence_production(session, days=days, job=job)
    return CadenceProductionResponse(**result)