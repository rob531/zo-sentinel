"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org

from .logic import api_key_inventory_audit

router = APIRouter(prefix="/api", tags=["api-key-inventory-audit"])


class ApiKeyEntryResponse(BaseModel):
    id: str
    label: str
    created_at: datetime
    hash_prefix: str


class OrgInventoryResponse(BaseModel):
    org_id: str
    org_name: str | None
    key_count: int
    keys: list[ApiKeyEntryResponse]
    missing_org_name: bool


class OrgWithoutKeysResponse(BaseModel):
    org_id: str
    org_name: str | None


class ApiKeyInventoryResponse(BaseModel):
    total_keys: int
    orgs: list[OrgInventoryResponse]
    orgs_without_keys: list[OrgWithoutKeysResponse]


@router.get("/audit/api-keys", response_model=ApiKeyInventoryResponse)
def audit_api_keys(db: Session = Depends(get_session)) -> ApiKeyInventoryResponse:
    payload = api_key_inventory_audit(db)
    return ApiKeyInventoryResponse(**payload)