"""HTTP surface for registry verdict-age distribution."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import registry_verdict_age

router = APIRouter(prefix="/api/registry", tags=["registry"])


class VerdictAgeBandResponse(BaseModel):
    band: str
    verdict: str
    risk_tier: str
    count: int


class RegistryVerdictAgeResponse(BaseModel):
    days: int
    bands: list[VerdictAgeBandResponse]
    never_assessed: int
    total: int


@router.get("/verdict-age", response_model=RegistryVerdictAgeResponse)
def get_registry_verdict_age(
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(get_session),
) -> RegistryVerdictAgeResponse:
    result = registry_verdict_age(db, days)
    return RegistryVerdictAgeResponse.model_validate(result)