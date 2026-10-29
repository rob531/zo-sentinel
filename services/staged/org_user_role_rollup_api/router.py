from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import Org, User

from .logic import get_org_user_role_rollup

router = APIRouter(prefix="/api")


class RoleCounts(BaseModel):
    owner: int = 0
    admin: int = 0
    analyst: int = 0
    viewer: int = 0
    other: int = 0


class OrgRoleRollup(BaseModel):
    org_id: str
    org_name: str
    created_at: str | None = None
    total_members: int
    roles: RoleCounts
    members_without_clerk_id: int
    members_joined_last_30d: int


class RoleRollupTotals(BaseModel):
    orgs: int
    members: int


class RoleRollupResponse(BaseModel):
    orgs: list[OrgRoleRollup]
    totals: RoleRollupTotals


@router.get(
    "/orgs/users/role-rollup",
    response_model=RoleRollupResponse,
    summary="Summarise organisation membership and role mix",
)
def org_user_role_rollup(
    days: int | None = Query(default=30, ge=1, le=365),
    session=Depends(get_session),
):
    return get_org_user_role_rollup(session, days=days)