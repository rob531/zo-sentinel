from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_registry_metadata_gaps

router = APIRouter(tags=["registry-metadata-gaps"])


class MetadataGaps(BaseModel):
    missing_description: int
    missing_url: int
    missing_trust_score: int
    missing_confidence: int
    missing_last_assessed: int
    missing_risk_tier: int
    missing_verdict_reasoning: int


class RegistrySourceSummary(BaseModel):
    registry_source: str
    total: int
    incomplete: int


class WorstOffender(BaseModel):
    server_id: str
    name: str | None
    registry_source: str | None
    missing: list[str]


class RegistryMetadataGapsResponse(BaseModel):
    total_servers: int
    gaps: MetadataGaps
    by_source: list[RegistrySourceSummary]
    worst_offenders: list[WorstOffender]
    limit: int


@router.get(
    "/registry/metadata-gaps",
    response_model=RegistryMetadataGapsResponse,
)
def get_registry_metadata_gaps_route(
    limit: int = Query(default=50, ge=0, le=500),
    session: Session = Depends(get_session),
) -> RegistryMetadataGapsResponse:
    return RegistryMetadataGapsResponse.model_validate(
        get_registry_metadata_gaps(limit=limit, session=session)
    )