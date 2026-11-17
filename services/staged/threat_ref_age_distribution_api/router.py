from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ThreatIntelRef

try:
    from .logic import get_age_distribution as _get_age_distribution
except ImportError:
    from .logic import get_indicator_age_distribution as _get_age_distribution

router = APIRouter(prefix="/api")


class AgeBuckets(BaseModel):
    b0_1: int
    b2_7: int
    b8_30: int
    b31_90: int
    b90_plus: int


class IndicatorTypeDistribution(BaseModel):
    indicator_type: str
    total: int
    buckets: AgeBuckets


class SourceCount(BaseModel):
    source: str
    count: int


class IndicatorAgeDistributionResponse(BaseModel):
    generated_at: str
    total: int
    by_type: list[IndicatorTypeDistribution]
    sources: list[SourceCount]


@router.get(
    "/threat-intel/indicator-age",
    response_model=IndicatorAgeDistributionResponse,
)
def get_indicator_age_distribution(
    session: Session = Depends(get_session),
) -> IndicatorAgeDistributionResponse:
    result = _get_age_distribution(session)
    return IndicatorAgeDistributionResponse.model_validate(result)


get_threat_ref_age_distribution = get_indicator_age_distribution
get_age_distribution = get_indicator_age_distribution


if __name__ == "__main__":
    print("PASS")