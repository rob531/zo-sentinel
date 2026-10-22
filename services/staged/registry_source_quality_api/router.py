from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_registry_source_quality

router = APIRouter(prefix="/api", tags=["registry"])


class RegistrySourceQualityItem(BaseModel):
    source: Optional[str]
    servers: int
    avg_trust_score: Optional[float]
    trusted_count: int
    risky_count: int
    stale_count: int
    last_seen_max: Optional[datetime]


class RegistrySourceQualityResponse(BaseModel):
    sources: List[RegistrySourceQualityItem]
    total_sources: int


@router.get(
    "/registry/source-quality",
    response_model=RegistrySourceQualityResponse,
)
def registry_source_quality(
    stale_days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> RegistrySourceQualityResponse:
    return get_registry_source_quality(session=session, stale_days=stale_days)