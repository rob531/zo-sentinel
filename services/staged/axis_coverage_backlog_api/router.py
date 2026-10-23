from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_axis_coverage


router = APIRouter(prefix="/api")


class AxisCoverage(BaseModel):
    axis_name: str
    scored_servers: int
    coverage_pct: float


class AxisCoverageResponse(BaseModel):
    total_servers: int
    never_scored: int
    fully_scored: int
    axes: List[AxisCoverage]


@router.get("/axes/coverage", response_model=AxisCoverageResponse)
def axis_coverage(session: Session = Depends(get_session)) -> AxisCoverageResponse:
    return get_axis_coverage(session)