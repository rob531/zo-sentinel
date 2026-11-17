"""HTTP surface for vulnerability advisory package rollups."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

from .logic import aggregate_packages

router = APIRouter(prefix="/api/vuln", tags=["vulnerability"])


class PackageRollup(BaseModel):
    ecosystem: str | None
    package: str | None
    advisory_count: int
    severity_counts: dict[str, int]
    affected_servers: int
    latest_published_at: datetime | None
    top_server_names: list[str]


class PackageRollupResponse(BaseModel):
    total_packages: int
    packages: list[PackageRollup]


@router.get("/packages", response_model=PackageRollupResponse)
def get_packages(db: Session = Depends(get_session)) -> PackageRollupResponse:
    result = aggregate_packages(db)
    return PackageRollupResponse(**result)


__all__ = ["McpServerRegistry", "PackageRollup", "PackageRollupResponse", "VulnAdvisory", "VulnLink", "router"]