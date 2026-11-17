"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

# The logic layer implements the actual data retrieval.
# It must return a mapping compatible with the response model below.
from .logic import get_api_key_usage

router = APIRouter(prefix="/api", tags=["api_key_usage"])


class KeyInfo(BaseModel):
    id: int
    org_id: int
    org_name: str
    label: str
    key_hash_preview: str = Field(..., description="First 8 chars of the hash followed by '...'")
    created_at: str
    age_days: int
    last_used_hint: None | str = None
    stale: bool


class ApiKeyUsageResponse(BaseModel):
    keys: list[KeyInfo]
    count: int
    stale_count: int


@router.get(
    "/org/api-keys",
    response_model=ApiKeyUsageResponse,
    summary="Read‑only view of API key inventory",
)
def get_api_keys(
    org_id: str | None = Query(
        default=None,
        description="Filter to a specific organisation ID (optional)",
    ),
    include_unused_days: int = Query(
        default=90,
        ge=0,
        description="Age in days beyond which a key is considered stale",
    ),
    db: Session = Depends(get_session),
) -> ApiKeyUsageResponse:
    """
    Return a list of API keys with organisational context and usage hints.

    The heavy lifting is performed in `services.staged.api_key_usage_service.logic`.
    """
    result = get_api_key_usage(db, org_id=org_id, include_unused_days=include_unused_days)
    return ApiKeyUsageResponse(**result)