"""HTTP surface for the organisation API key inventory rollup."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import org_api_key_org_rollup

router = APIRouter(prefix="/api", tags=["org-api-key-org-rollup-api"])


class OrgApiKeyOrgRollupEntry(BaseModel):
    org_id: int
    org_name: str
    key_count: int
    oldest_key_created_at: datetime | None
    newest_key_created_at: datetime | None
    keys_per_day_avg: float


class OrgApiKeyOrgRollupTotals(BaseModel):
    org_count: int
    key_count: int
    orgs_without_keys: int


class OrgApiKeyOrgRollupResponse(BaseModel):
    orgs: list[OrgApiKeyOrgRollupEntry]
    totals: OrgApiKeyOrgRollupTotals


@router.get(
    "/orgs/api-keys/rollup",
    response_model=OrgApiKeyOrgRollupResponse,
)
def get_org_api_key_org_rollup(
    db: Session = Depends(get_session),
) -> OrgApiKeyOrgRollupResponse:
    payload = org_api_key_org_rollup(db)
    return OrgApiKeyOrgRollupResponse(**payload)