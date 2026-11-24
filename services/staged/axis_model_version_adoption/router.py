"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import model_version_adoption

router = APIRouter(prefix="/api", tags=["axis_model_version_adoption"])


class ModelVersionAdoptionResponse(BaseModel):
    days: int
    generated_at: str
    totals: Dict[str, int]
    by_model_version: List[Dict[str, Any]]
    by_adapter_sha256: List[Dict[str, Any]]
    by_decision_rule_version: List[Dict[str, Any]]
    mixed_model_servers: int


@router.get(
    "/axis/model-version-adoption",
    response_model=ModelVersionAdoptionResponse,
    summary="Report model version adoption statistics",
)
def get_model_version_adoption(
    days: int = 90,
    db: Session = Depends(get_session),
) -> ModelVersionAdoptionResponse:
    """Return adoption metrics for LLM model versions, adapters, and decision rules.

    Args:
        days: Number of days to look back (1‑365). Defaults to 90.
        db: SQLAlchemy session injected by FastAPI.

    Returns:
        A structured response containing totals and breakdowns.
    """
    if not (1 <= days <= 365):
        raise HTTPException(
            status_code=400,
            detail="Query parameter `days` must be between 1 and 365 inclusive.",
        )
    data = model_version_adoption(db, days)
    return ModelVersionAdoptionResponse(**data)