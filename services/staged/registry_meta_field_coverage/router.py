"""HTTP surface for registry metadata field coverage."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_registry_meta_field_coverage

router = APIRouter(prefix="/api", tags=["registry-meta-field-coverage"])


class MetaKeyCoverage(BaseModel):
    key: str
    count: int


class RegistrySourceCoverage(BaseModel):
    registry_source: str
    servers: int
    meta_present: int
    meta_present_pct: float
    meta_keys: list[MetaKeyCoverage]


class RegistryMetaFieldCoverageResponse(BaseModel):
    sources: list[RegistrySourceCoverage]
    total_servers: int
    servers_with_meta: int


@router.get(
    "/registry/meta/coverage",
    response_model=RegistryMetaFieldCoverageResponse,
)
def get_registry_meta_coverage(
    db: Session = Depends(get_session),
) -> RegistryMetaFieldCoverageResponse:
    coverage = get_registry_meta_field_coverage(db)
    return RegistryMetaFieldCoverageResponse(**coverage)