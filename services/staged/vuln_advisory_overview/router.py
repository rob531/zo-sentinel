from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from .logic import get_vuln_advisory_overview

router = APIRouter(prefix="/vuln", tags=["vuln-advisory-overview"])


class TopPackageResponse(BaseModel):
    package: str
    count: int


class AdvisoryFiltersResponse(BaseModel):
    severity: str | None
    ecosystem: str | None


class VulnAdvisoryOverviewResponse(BaseModel):
    total: int
    by_severity: dict[str, int]
    by_ecosystem: dict[str, int]
    top_packages: list[TopPackageResponse]
    linked_advisories: int
    filters: AdvisoryFiltersResponse


@router.get("/advisories", response_model=VulnAdvisoryOverviewResponse)
async def read_vuln_advisory_overview(
    severity: str | None = Query(default=None),
    ecosystem: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> VulnAdvisoryOverviewResponse:
    return await get_vuln_advisory_overview(
        session=session,
        severity=severity,
        ecosystem=ecosystem,
        limit=limit,
    )