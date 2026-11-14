"""HTTP surface for the registry source mix service."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import registry_source_mix

router = APIRouter(prefix="/api/registry", tags=["registry"])


class SourceMixRow(BaseModel):
    source: str
    server_count: int
    tier_counts: dict[str, int]
    avg_trust_score: float | None
    mean_overall_p_top: float | None


class SourceMixResponse(BaseModel):
    days: int
    generated_at: str
    total_servers: int
    sources: list[SourceMixRow]


@router.get("/source-mix", response_model=SourceMixResponse)
def get_source_mix(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> SourceMixResponse:
    result = registry_source_mix(db, days)
    return SourceMixResponse(
        days=days,
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_servers=result["total_servers"],
        sources=[SourceMixRow(**row) for row in result["sources"]],
    )