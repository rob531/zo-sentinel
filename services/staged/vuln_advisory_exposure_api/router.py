"""HTTP surface for vulnerability advisory exposure metrics."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_exposure_summary

router = APIRouter(prefix="/api", tags=["vuln-advisory-exposure"])


class SeverityCount(BaseModel):
    severity: str
    count: int


class EcosystemCount(BaseModel):
    ecosystem: str
    count: int


class TopServer(BaseModel):
    server_id: str
    name: str | None
    risk_tier: str | None
    advisory_count: int


class ExposureResponse(BaseModel):
    total_advisories: int
    total_links: int
    servers_with_advisories: int
    avg_match_confidence: float | None
    by_severity: list[SeverityCount]
    by_ecosystem: list[EcosystemCount]
    top_servers: list[TopServer]


@router.get("/vuln/exposure", response_model=ExposureResponse)
def get_vuln_exposure(db: Session = Depends(get_session)) -> ExposureResponse:
    return ExposureResponse.model_validate(get_exposure_summary(db))