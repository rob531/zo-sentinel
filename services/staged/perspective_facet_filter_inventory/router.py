from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_facet_filter_inventory as build_facet_filter_inventory

router = APIRouter(prefix="/api")


@router.get("/perspectives/facet-filter-inventory")
def facet_filter_inventory(
    org_id: str,
    session: Session = Depends(get_session),
) -> dict:
    return build_facet_filter_inventory(session, org_id)