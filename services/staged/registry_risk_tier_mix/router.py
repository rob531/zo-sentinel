"""HTTP surface for the registry risk-tier mix service."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_tier_mix

router = APIRouter(prefix="/api/registry", tags=["registry"])


@router.get("/tier-mix")
def registry_tier_mix(
    registry_source: str | None = None,
    db: Session = Depends(get_session),
) -> dict:
    return get_tier_mix(db, registry_source=registry_source)