"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Org, User, ApiKey

from .logic import get_org_tenancy_inventory

router = APIRouter(prefix="/api/org_tenancy_inventory", tags=["org_tenancy_inventory"])


class TenancyInventoryResponse(BaseModel):
    orgs: list[dict[str, int]]


@router.get("/inventory", response_model=TenancyInventoryResponse)
def get_org_tenancy_inventory(db: Session = Depends(get_session)) -> TenancyInventoryResponse:
    inventory = get_org_tenancy_inventory(db)
    return TenancyInventoryResponse(orgs=inventory)