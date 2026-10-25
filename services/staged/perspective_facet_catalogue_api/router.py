from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Perspective
from .logic import get_facet_catalogue as get_facet_catalogue_data

router = APIRouter(prefix="/api")


class FacetValue(BaseModel):
    value: str
    perspective_count: int


class Facet(BaseModel):
    key: str
    value_count: int
    values: list[FacetValue]


class FacetCatalogueResponse(BaseModel):
    facets: list[Facet]
    total_perspectives: int


@router.get(
    "/perspectives/facet-catalogue",
    response_model=FacetCatalogueResponse,
)
async def get_facet_catalogue(
    perspective_id: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> FacetCatalogueResponse:
    result = await get_facet_catalogue_data(
        session=session,
        perspective_id=perspective_id,
    )
    return FacetCatalogueResponse.model_validate(result)