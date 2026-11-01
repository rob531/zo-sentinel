"""HTTP surface for registry family summaries."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_registry_family_summary

router = APIRouter(prefix="/api/registry", tags=["registry"])


class RegistryFamilySummary(BaseModel):
    family: str
    member_count: int
    tiers: dict[str, int]
    avg_trust: float


class RegistryFamilySummaryResponse(BaseModel):
    families: list[RegistryFamilySummary]


@router.get(
    "/families/summary",
    response_model=RegistryFamilySummaryResponse,
)
def get_registry_families_summary(
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_session),
) -> RegistryFamilySummaryResponse:
    return RegistryFamilySummaryResponse(
        families=get_registry_family_summary(db, limit=limit)
    )


if __name__ == "__main__":
    print("PASS")