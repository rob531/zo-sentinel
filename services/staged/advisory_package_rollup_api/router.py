"""HTTP surface for advisory package rollups."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_package_rollups

router = APIRouter(prefix="/api", tags=["advisory-package-rollup"])


class PackageRollup(BaseModel):
    package: str
    ecosystem: str | None
    severity_max: str
    advisory_count: int
    linked_server_count: int


class AdvisoryFilters(BaseModel):
    ecosystem: str | None
    severity: str | None


class PackageRollupResponse(BaseModel):
    packages: list[PackageRollup]
    total: int
    filters: AdvisoryFilters


@router.get(
    "/vuln/advisories/packages",
    response_model=PackageRollupResponse,
)
def list_package_rollups(
    db: Session = Depends(get_session),
    ecosystem: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> PackageRollupResponse:
    packages = get_package_rollups(
        db,
        ecosystem=ecosystem,
        severity=severity,
        limit=limit,
    )
    return PackageRollupResponse(
        packages=[PackageRollup.model_validate(package) for package in packages],
        total=len(packages),
        filters=AdvisoryFilters(ecosystem=ecosystem, severity=severity),
    )