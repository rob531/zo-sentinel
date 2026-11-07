"""HTTP surface for the perspective facet-usage census service."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import facet_usage_census

router = APIRouter(
    prefix="/api/perspectives",
    tags=["perspectives"],
)


class OrgPerspectiveCount(BaseModel):
    org_id: int | None
    count: int


class FacetUsageCensusResponse(BaseModel):
    total_perspectives: int
    per_org: list[OrgPerspectiveCount]
    facet_keys: list[str]
    facet_key_counts: dict[str, int]
    perspectives_without_snapshots: list[int]
    generated_at: datetime


@router.get(
    "/facet-usage-census",
    response_model=FacetUsageCensusResponse,
)
def get_facet_usage_census(
    db: Session = Depends(get_session),
) -> FacetUsageCensusResponse:
    return FacetUsageCensusResponse(**facet_usage_census(db))