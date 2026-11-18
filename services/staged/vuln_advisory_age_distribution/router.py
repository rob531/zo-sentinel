"""HTTP surface for vulnerability advisory age and severity distribution."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import advisory_age_distribution

router = APIRouter(prefix="/api/vuln", tags=["vuln_advisory_age_distribution"])


class SeverityCount(BaseModel):
    severity: str
    count: int


class EcosystemCount(BaseModel):
    ecosystem: str
    count: int


class AgeBucketCount(BaseModel):
    bucket: str
    count: int


class AdvisoryAgeDistributionResponse(BaseModel):
    total: int
    by_severity: list[SeverityCount]
    by_ecosystem: list[EcosystemCount]
    age_buckets: list[AgeBucketCount]
    distinct_packages: int
    feeds: list[str]


@router.get(
    "/advisory-age",
    response_model=AdvisoryAgeDistributionResponse,
)
def get_advisory_age_distribution(
    db: Session = Depends(get_session),
) -> AdvisoryAgeDistributionResponse:
    return AdvisoryAgeDistributionResponse(**advisory_age_distribution(db))