from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User
from .logic import get_org_access_inventory

router = APIRouter(prefix="/api")


class RoleCounts(BaseModel):
    admin: int
    member: int
    other: int


class InventoryTotals(BaseModel):
    orgs: int
    users: int
    api_keys: int


class OrgAccessInventoryItem(BaseModel):
    org_id: str
    name: str
    created_at: str
    user_count: int
    api_key_count: int
    roles: RoleCounts
    users_without_keys: int


class OrgAccessInventoryResponse(BaseModel):
    generated_at: str
    totals: InventoryTotals
    orgs: list[OrgAccessInventoryItem]
    unattached_users: int
    orgs_without_users: int


@router.get(
    "/orgs/access-inventory",
    response_model=OrgAccessInventoryResponse,
)
def org_access_inventory(
    org_id: str | None = None,
    session: Session = Depends(get_session),
) -> OrgAccessInventoryResponse:
    return get_org_access_inventory(session, org_id)