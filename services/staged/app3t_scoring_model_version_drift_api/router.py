"""HTTP surface for scoring model-version drift."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import get_model_versions

router = APIRouter(prefix="/api", tags=["scoring"])


class ModelVersionEntry(BaseModel):
    decision_rule_version: str
    model_version: str
    adapter_sha256: str
    rows: int
    first_seen: datetime | None
    last_seen: datetime | None


class ModelVersionTotals(BaseModel):
    rows: int
    distinct_versions: int
    distinct_models: int
    distinct_adapters: int


class ModelVersionsResponse(BaseModel):
    versions: list[ModelVersionEntry]
    totals: ModelVersionTotals


@router.get(
    "/scoring/model-versions",
    response_model=ModelVersionsResponse,
)
def get_scoring_model_versions(
    db: Session = Depends(get_session),
) -> ModelVersionsResponse:
    return ModelVersionsResponse(**get_model_versions(db))