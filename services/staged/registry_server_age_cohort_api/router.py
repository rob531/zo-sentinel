from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_age_cohorts

router = APIRouter(prefix="/api", tags=["registry"])


class AgeCohort(BaseModel):
    bucket: str
    count: int = Field(ge=0)
    pct: float = Field(ge=0)
    tier_mix: dict[str, int]
    unscored_count: int = Field(ge=0)


class AgeCohortResponse(BaseModel):
    days: int = Field(ge=1, le=365)
    total_servers: int = Field(ge=0)
    cohorts: list[AgeCohort]


@router.get(
    "/registry/age-cohorts",
    response_model=AgeCohortResponse,
    summary="Get registry servers grouped by age cohort",
)
def age_cohorts(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> dict:
    return get_age_cohorts(days=days, session=session)


if __name__ == "__main__":
    print("PASS")