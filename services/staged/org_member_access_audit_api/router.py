"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import org_member_access_audit

router = APIRouter(prefix="/api", tags=["org_member_access_audit"])


class Member(BaseModel):
    user_id: str
    email: str | None = None
    role: str | None = None
    created_at: str | None = None


class OrgInfo(BaseModel):
    org_id: str
    org_name: str | None = None
    created_at: str | None = None
    member_count: int
    members: list[Member]


class OrgMemberAuditResponse(BaseModel):
    orgs: list[OrgInfo]
    total_orgs: int
    total_members: int


@router.get(
    "/orgs/members",
    response_model=OrgMemberAuditResponse,
    summary="Audit organisation members",
)
def get_org_members(
    org_id: str | None = Query(default=None, description="Filter by organisation ID"),
    db: Session = Depends(get_session),
) -> OrgMemberAuditResponse:
    """
    Retrieve a list of organisations with their members.

    - If `org_id` is provided, only that organisation is returned (empty list if not found).
    - Organisations without members are included with `member_count` set to 0.
    """
    result = org_member_access_audit(db, org_id)
    return result