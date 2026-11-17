from datetime import datetime
from typing import Dict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory
from .logic import get_severity_mix

router = APIRouter()


class AdvisorySeverityMixResponse(BaseModel):
    days: int
    by_severity: Dict[str, int]
    by_ecosystem: Dict[str, int]
    by_feed: Dict[str, int]
    missing_aliases: int
    missing_package: int
    total_advisories: int
    generated_at: datetime


@router.get(
    "/vuln/advisories/severity-mix",
    response_model=AdvisorySeverityMixResponse,
)
def advisory_severity_mix(
    days: int = Query(default=30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> AdvisorySeverityMixResponse:
    result = get_severity_mix(session, days)
    return AdvisorySeverityMixResponse.parse_obj(result)