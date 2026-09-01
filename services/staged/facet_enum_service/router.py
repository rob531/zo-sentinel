from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from pydantic import BaseModel

from app.db import get_session
from .logic import get_facet_values


router = APIRouter(prefix="/api")


class ValueCount(BaseModel):
    value: str
    count: int


class FacetResponse(BaseModel):
    facet: str
    values: List[ValueCount]


@router.get(
    "/facets/{facet_name}",
    response_model=FacetResponse,
    summary="Enumerate distinct values for a given facet",
)
def enumerate_facet(
    facet_name: str,
    session: Session = Depends(get_session),
) -> FacetResponse:
    """
    Return distinct values and their occurrence counts for the requested facet.
    The heavy‑lifting is delegated to `services.staged.facet_enum_service.logic.get_facet_values`.
    """
    try:
        raw_values = get_facet_values(session=session, facet_name=facet_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    values = [ValueCount(value=v, count=c) for v, c in raw_values]
    return FacetResponse(facet=facet_name, values=values)