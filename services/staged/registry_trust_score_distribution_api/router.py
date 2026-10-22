from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import compute_distribution

router = APIRouter(tags=["registry"])


class TrustScoreBucket(BaseModel):
    bucket_start: float
    bucket_end: float
    count: int


class TrustScoreDistributionResponse(BaseModel):
    registry_source: Optional[str]
    bucket_size: int
    total: int
    buckets: List[TrustScoreBucket]
    min: Optional[float]
    max: Optional[float]
    mean: Optional[float]
    median: Optional[float]


@router.get(
    "/api/registry/trust-score-distribution",
    response_model=TrustScoreDistributionResponse,
)
def trust_score_distribution(
    registry_source: Optional[str] = None,
    bucket_size: int = 10,
    session: Session = Depends(get_session),
) -> dict:
    return compute_distribution(
        session=session,
        registry_source=registry_source,
        bucket_size=bucket_size,
    )