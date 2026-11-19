"""HTTP surface for the unauthenticated-access exposure matrix."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_unauth_access_matrix

router = APIRouter(prefix="/api", tags=["unauth-access"])


class ExposureStateResponse(BaseModel):
    unauth_access: str
    count: int


class TierCountResponse(BaseModel):
    risk_tier: str
    count: int


class SourceCountResponse(BaseModel):
    source: str
    count: int


class ServerExposureResponse(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    unauth_access: str
    source: str
    updated_at: str


class MatrixTotalsResponse(BaseModel):
    servers: int
    exposed_servers: int


class UnauthAccessMatrixResponse(BaseModel):
    exposure_states: list[ExposureStateResponse]
    by_tier: list[TierCountResponse]
    by_source: list[SourceCountResponse]
    servers: list[ServerExposureResponse]
    totals: MatrixTotalsResponse


@router.get(
    "/unauth-access/matrix",
    response_model=UnauthAccessMatrixResponse,
)
def get_matrix(db: Session = Depends(get_session)) -> UnauthAccessMatrixResponse:
    payload = get_unauth_access_matrix(db)
    return UnauthAccessMatrixResponse(
        exposure_states=[
            ExposureStateResponse(**item) for item in payload["exposure_states"]
        ],
        by_tier=[TierCountResponse(**item) for item in payload["by_tier"]],
        by_source=[SourceCountResponse(**item) for item in payload["by_source"]],
        servers=[ServerExposureResponse(**item) for item in payload["servers"]],
        totals=MatrixTotalsResponse(**payload["totals"]),
    )