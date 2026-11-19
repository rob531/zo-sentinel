"""HTTP surface for registry meta-tag coverage."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import registry_meta_tag_coverage

router = APIRouter(prefix="/api/registry", tags=["registry"])


class SourceCoverage(BaseModel):
    source: str
    servers: int
    with_meta: int
    coverage_pct: float
    top_keys: list[str]
    top_key: str


class CoverageTotals(BaseModel):
    servers: int
    with_meta: int


class MetaTagCoverageResponse(BaseModel):
    sources: list[SourceCoverage]
    totals: CoverageTotals


@router.get(
    "/meta-tag-coverage",
    response_model=MetaTagCoverageResponse,
)
def get_meta_tag_coverage(
    source: str = Query(..., min_length=1),
    db: Session = Depends(get_session),
) -> MetaTagCoverageResponse:
    return registry_meta_tag_coverage(db, source)