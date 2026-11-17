"""HTTP surface for adapter SHA drift reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import adapter_sha_drift_stats

router = APIRouter(prefix="/api", tags=["adapter-sha-drift"])


class AdapterCount(BaseModel):
    adapter_sha256: str
    servers: int


class ModelVersionDrift(BaseModel):
    model_version: str
    distinct_adapters: int
    distinct_rule_versions: int
    scores: int
    servers: int
    adapters: list[AdapterCount]


class AdapterShaDriftResponse(BaseModel):
    model_versions: list[ModelVersionDrift]
    total_scores: int


@router.get(
    "/scores/adapter-drift",
    response_model=AdapterShaDriftResponse,
)
def get_adapter_sha_drift(
    db: Session = Depends(get_session),
) -> AdapterShaDriftResponse:
    return AdapterShaDriftResponse(**adapter_sha_drift_stats(db))