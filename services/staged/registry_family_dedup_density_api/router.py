from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_family_density

router = APIRouter(prefix="/api", tags=["registry"])


class FamilyDensity(BaseModel):
    family: str
    member_count: int
    sources: list[str]
    risk_tiers: list[str]


class FamilyDensityResponse(BaseModel):
    limit: int
    distinct_family_count: int
    duplicate_family_count: int
    duplicate_row_count: int
    families: list[FamilyDensity]


@router.get(
    "/registry/family-density",
    response_model=FamilyDensityResponse,
    summary="Measure duplicate density across MCP registry server families",
)
def family_density(
    limit: int = Query(25, description="Maximum number of duplicate families to return"),
    session: Session = Depends(get_session),
) -> FamilyDensityResponse:
    limit = max(1, min(limit, 200))
    result = get_family_density(session, limit)

    if isinstance(result, dict):
        payload = result
    else:
        payload = {
            "distinct_family_count": result.distinct_family_count,
            "duplicate_family_count": result.duplicate_family_count,
            "duplicate_row_count": result.duplicate_row_count,
            "families": result.families,
        }

    return FamilyDensityResponse(
        limit=limit,
        distinct_family_count=payload["distinct_family_count"],
        duplicate_family_count=payload["duplicate_family_count"],
        duplicate_row_count=payload["duplicate_row_count"],
        families=payload["families"],
    )