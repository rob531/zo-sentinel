from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from .logic import compute_freshness_sla

router = APIRouter(prefix="/api", tags=["scores"])


class InitialLatencyCounts(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    bucket_0_24h: int = Field(alias="0_24h")
    bucket_24_72h: int = Field(alias="24_72h")
    bucket_72h_plus: int = Field(alias="72h_plus")
    never_scored: int


class RescoreStalenessCounts(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    bucket_0_7d: int = Field(alias="0_7d")
    bucket_7_30d: int = Field(alias="7_30d")
    bucket_30d_plus: int = Field(alias="30d_plus")
    never_scored: int


class WorstOffender(BaseModel):
    server_id: str
    first_seen: datetime
    last_scored_at: datetime | None
    hours_to_first_score: float | None
    risk_tier: str | None


class FreshnessSlaResponse(BaseModel):
    generated_at: datetime
    total_servers: int
    initial_latency: InitialLatencyCounts
    rescore_staleness: RescoreStalenessCounts
    sla_first_verdict_within_24h_pct: float
    worst_offenders: list[WorstOffender]


@router.get("/scores/freshness-sla", response_model=FreshnessSlaResponse)
def get_score_freshness_sla(
    session: Session = Depends(get_session),
) -> FreshnessSlaResponse:
    return FreshnessSlaResponse.model_validate(
        compute_freshness_sla(
            session=session,
            registry_model=McpServerRegistry,
            score_model=McpLlmAxisScore,
        )
    )