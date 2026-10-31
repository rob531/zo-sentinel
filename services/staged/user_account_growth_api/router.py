from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session
from app.models import User
from .logic import get_user_growth


class GrowthSeriesPoint(BaseModel):
    date: str
    count: int


class UserGrowthResponse(BaseModel):
    days: int
    series: list[GrowthSeriesPoint]
    total: int
    by_role: dict[str, int]


router = APIRouter(prefix="/api")


@router.get("/users/growth", response_model=UserGrowthResponse)
def user_account_growth(days: int = 30, db=Depends(get_session)):
    return get_user_growth(db=db, days=days)