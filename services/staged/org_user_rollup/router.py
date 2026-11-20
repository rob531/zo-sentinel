"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` for the organisation user and API-key rollup endpoint.
Thin: injects the real Session via Depends(get_session) and delegates to
logic.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_org_rollup

router = APIRouter(prefix="/api", tags=["org_user_rollup"])


class UsersByRoleResponse(BaseModel):
    admin: int
    analyst: int
    viewer: int


class OrgRollupResponse(BaseModel):
    org_id: str
    name: str
    created_at: str
    user_count: int
    api_key_count: int
    users_by_role: UsersByRoleResponse


class OrgUserRollupResponse(BaseModel):
    total_orgs: int
    orgs: list[OrgRollupResponse]


@router.get("/orgs/rollup", response_model=OrgUserRollupResponse)
def get_org_user_rollup(
    db: Session = Depends(get_session),
) -> OrgUserRollupResponse:
    return OrgUserRollupResponse(**get_org_rollup(db))