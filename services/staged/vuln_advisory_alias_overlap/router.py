"""HTTP surface for vulnerability-advisory alias overlap analysis."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import find_alias_overlaps

router = APIRouter(prefix="/api", tags=["vulns"])


class AdvisoryAliasOverlap(BaseModel):
    alias: str
    count: int
    advisories: list["OverlappingAdvisory"]


class OverlappingAdvisory(BaseModel):
    advisory_id: str
    feed: str
    package: str
    severity: str


class AliasOverlapResponse(BaseModel):
    min_shared: int
    total_advisories: int
    overlaps: list[AdvisoryAliasOverlap]


@router.get("/vulns/alias-overlap", response_model=AliasOverlapResponse)
def get_alias_overlap(
    min_shared: int = Query(default=2, ge=1),
    db: Session = Depends(get_session),
) -> AliasOverlapResponse:
    result = find_alias_overlaps(db, min_shared=min_shared)
    return AliasOverlapResponse(
        min_shared=result["min_shared"],
        total_advisories=result["total_advisories"],
        overlaps=result["overlaps"],
    )


AliasOverlapResponse.model_rebuild()