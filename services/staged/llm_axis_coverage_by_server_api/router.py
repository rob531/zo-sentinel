from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import AxisCoverageResponse, get_axis_coverage

router = APIRouter(prefix="/api", tags=["scoring"])


@router.get("/scoring/axis-coverage", response_model=AxisCoverageResponse)
def get_axis_coverage_by_server_api(
    session: Session = Depends(get_session),
) -> AxisCoverageResponse:
    return get_axis_coverage(session)