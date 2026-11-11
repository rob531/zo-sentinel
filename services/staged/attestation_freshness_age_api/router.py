from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat
from .logic import get_freshness_age

router = APIRouter(prefix="/api", tags=["attestations"])


class FreshnessAgeBucket(BaseModel):
    bucket_start: str
    bucket_end: str
    count: int = Field(ge=0)


class FreshnessAgeResponse(BaseModel):
    buckets: list[FreshnessAgeBucket]
    total: int = Field(ge=0)
    stale_count: int = Field(ge=0)
    generated_at: str


@router.get(
    "/attestations/freshness-age",
    response_model=FreshnessAgeResponse,
    summary="Summarize attestation freshness by age bucket",
)
def attestation_freshness_age(
    bucket_days: int = Query(default=7, ge=1, le=365),
    session: Session = Depends(get_session),
) -> FreshnessAgeResponse:
    payload = get_freshness_age(session=session, bucket_days=bucket_days)
    return FreshnessAgeResponse.model_validate(payload)