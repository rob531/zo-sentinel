from datetime import date, datetime
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_org_seat_growth

router = APIRouter(prefix="/api")


class OrgSeatGrowthEntry(BaseModel):
    org_id: int
    name: str
    seats: int
    admins: int
    members: int
    created_at: datetime


class SignupSeriesEntry(BaseModel):
    date: date
    count: int


class OrgSeatGrowthResponse(BaseModel):
    days: int
    orgs: List[OrgSeatGrowthEntry]
    signup_series: List[SignupSeriesEntry]
    total_users: int
    generated_at: datetime


@router.get(
    "/orgs/seat-growth",
    response_model=OrgSeatGrowthResponse,
    summary="Report organization seat growth and recent signups",
)
def org_seat_growth(
    days: int = 30,
    session: Session = Depends(get_session),
) -> OrgSeatGrowthResponse:
    return get_org_seat_growth(session, days)