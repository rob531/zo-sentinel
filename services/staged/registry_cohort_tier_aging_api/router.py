from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_cohort_tier_aging

router = APIRouter(prefix="/api", tags=["registry"])


class CohortTierAging(BaseModel):
    cohort: str
    servers: int = Field(ge=0)
    tiers: dict[str, int]
    mean_scan_count: float = Field(ge=0)
    never_assessed: int = Field(ge=0)
    stale_over_window: int = Field(ge=0)


class RegistryCohortTierAgingResponse(BaseModel):
    cohorts: list[CohortTierAging]
    totals: dict[str, int]


@router.get(
    "/registry/cohort-tier-aging",
    response_model=RegistryCohortTierAgingResponse,
)
def registry_cohort_tier_aging(
    age_days: int = Query(30, ge=0),
    session: Session = Depends(get_session),
) -> RegistryCohortTierAgingResponse:
    return get_cohort_tier_aging(session=session, age_days=age_days)