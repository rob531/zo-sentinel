"""HTTP surface for unauthenticated-access exposure summaries."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_server_unauth_summary, get_unauth_summary

router = APIRouter(prefix="/api/exposure", tags=["exposure"])


class ExposureTotals(BaseModel):
    servers: int
    rows: int


class UnauthAccessGroup(BaseModel):
    unauth_access: str
    servers: int
    rows: int


class SourceGroup(BaseModel):
    source: str
    servers: int
    rows: int


class RiskTierGroup(BaseModel):
    risk_tier: str
    servers: int


class ServerExposure(BaseModel):
    server_id: str
    name: str | None = None
    url: str | None = None
    unauth_access: str | None = None
    source: str | None = None
    risk_tier: str | None = None
    trust_score: float | None = None
    updated_at: datetime | None = None


class UnauthSummaryResponse(BaseModel):
    generated_at: datetime
    totals: ExposureTotals
    by_unauth_access: list[UnauthAccessGroup]
    by_source: list[SourceGroup]
    by_risk_tier: list[RiskTierGroup]
    servers: list[ServerExposure]


class ServerUnauthSummaryResponse(BaseModel):
    server_id: str
    found: bool
    exposures: list[ServerExposure]


@router.get("/unauth-summary", response_model=UnauthSummaryResponse)
def get_unauth_exposure_summary(
    db: Session = Depends(get_session),
) -> UnauthSummaryResponse:
    return UnauthSummaryResponse(**get_unauth_summary(db))


@router.get(
    "/unauth-summary/{server_id}",
    response_model=ServerUnauthSummaryResponse,
)
def get_server_unauth_exposure_summary(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerUnauthSummaryResponse:
    return ServerUnauthSummaryResponse(
        **get_server_unauth_summary(db, server_id)
    )