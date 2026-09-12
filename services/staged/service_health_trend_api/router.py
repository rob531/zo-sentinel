from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .schemas import HealthTrendResponse
from .logic import get_health_trend, get_session

router = APIRouter()


@router.get("/api/service/health/trend", response_model=HealthTrendResponse)
def fetch_health_trend(window_hours: int = 24, session: Session = Depends(get_session)):
    return get_health_trend(window_hours=window_hours, session=session)