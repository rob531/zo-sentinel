from __future__ import annotations

from datetime import date
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_signup_timeline as _get_signup_timeline


class UserSignupPoint(BaseModel):
    date: date
    signups: int
    cumulative: int


class UserSignupOrg(BaseModel):
    org_id: int
    org_name: str
    signups: int


class UserSignupTimelineResponse(BaseModel):
    days: int
    series: List[UserSignupPoint]
    by_org: List[UserSignupOrg]


router = APIRouter(prefix="/api")


@router.get(
    "/users/signup-timeline",
    response_model=UserSignupTimelineResponse,
)
def get_user_signup_timeline(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> UserSignupTimelineResponse:
    return _get_signup_timeline(session, days)