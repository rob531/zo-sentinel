from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_org_seat_inventory


router = APIRouter(prefix="/api", tags=["organisation seat inventory"])


class OrgSeatInventoryItem(BaseModel):
    org_id: int
    name: str
    user_count: int
    admin_count: int
    member_count: int
    api_key_count: int
    oldest_user_at: Optional[datetime] = None
    newest_user_at: Optional[datetime] = None
    days_since_newest_user: Optional[int] = None


class OrgSeatInventoryResponse(BaseModel):
    total_orgs: int
    orgs: List[OrgSeatInventoryItem]


@router.get(
    "/orgs/seats",
    response_model=OrgSeatInventoryResponse,
    summary="Report per-organisation seat and credential inventory",
)
def read_org_seat_inventory(
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> OrgSeatInventoryResponse:
    return get_org_seat_inventory(session, limit=limit)