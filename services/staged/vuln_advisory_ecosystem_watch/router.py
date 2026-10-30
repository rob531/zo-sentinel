from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_ecosystem_watch

router = APIRouter(prefix="/api")


class SeverityMix(BaseModel):
    critical: int
    high: int
    medium: int
    low: int
    unknown: int


class EcosystemWatchItem(BaseModel):
    ecosystem: str
    advisory_count: int
    distinct_servers: int
    severity_mix: SeverityMix


class TopPackage(BaseModel):
    package: str
    ecosystem: str
    count: int


class EcosystemWatchResponse(BaseModel):
    total_advisories: int
    ecosystems: List[EcosystemWatchItem]
    top_packages: List[TopPackage]


@router.get(
    "/vuln/advisories/ecosystem-watch",
    response_model=EcosystemWatchResponse,
)
def ecosystem_watch(
    severity: Optional[str] = Query(default=None),
    ecosystem: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
):
    return get_ecosystem_watch(
        session,
        severity=severity,
        ecosystem=ecosystem,
        days=days,
    )