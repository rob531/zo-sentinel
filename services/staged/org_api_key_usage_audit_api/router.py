"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

# The logic layer provides the core data‑gathering function.
# It returns a dict matching the response model defined below.
from .logic import get_org_api_key_audit

router = APIRouter(prefix="/api", tags=["org_api_key_usage_audit"])


class KeyInfo(BaseModel):
    id: str
    label: str
    created_at: datetime
    owner_email: Optional[str] = None
    owner_role: Optional[str] = None
    key_hash_prefix: str = Field(..., alias="key_hash_prefix")


class CountsInfo(BaseModel):
    keys: int
    distinct_owners: int
    unlabelled: int


class AuditResponse(BaseModel):
    org_id: str
    org_name: str
    keys: List[KeyInfo]
    counts: CountsInfo
    oldest_created_at: Optional[datetime] = None


@router.get(
    "/orgs/{org_id}/api-key-audit",
    response_model=AuditResponse,
    name="org_api_key_usage_audit",
)
def get_audit(org_id: str, db: Session = Depends(get_session)) -> AuditResponse:
    """
    Retrieve API‑key usage audit information for a given organisation.
    """
    data = get_org_api_key_audit(db, org_id)
    return AuditResponse(**data)