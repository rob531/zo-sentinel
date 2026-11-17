from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_freshness_surface_stats, FreshnessSurfaceStatsResponse

router = APIRouter(prefix="/api")


@router.get(
    "/freshness/surfaces",
    response_model=FreshnessSurfaceStatsResponse,
    summary="Get freshness statistics per surface",
)
def freshness_surfaces(session: Session = Depends(get_session)):
    return get_freshness_surface_stats(session)