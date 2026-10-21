from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory
from .logic import get_advisory_ageing

router = APIRouter()


class EcosystemAgeing(BaseModel):
    ecosystem: str
    total: int
    older_90d: int


class AdvisoryAgeingResponse(BaseModel):
    total: int
    age_buckets: dict[str, int]
    ecosystems: list[EcosystemAgeing]
    unknown_published_at: int
    oldest_published_at: Optional[str]
    latest_fetched_at: Optional[str]


@router.get(
    "/vuln/advisory-ageing",
    response_model=AdvisoryAgeingResponse,
    response_model_exclude_none=False,
)
def advisory_ageing(
    ecosystem: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    return get_advisory_ageing(session, ecosystem)


if __name__ == "__main__":
    print("PASS")