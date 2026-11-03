"""HTTP surface for vulnerability supply-chain exposure reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_supply_chain_exposure

router = APIRouter(prefix="/api", tags=["vuln-supply-chain"])


class ExposedServer(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    advisory_count: int
    max_severity: str | None = None


class SupplyChainExposureResponse(BaseModel):
    advisories: int
    links: int
    servers_affected: int
    by_severity: dict[str, int]
    by_ecosystem: dict[str, int]
    top_servers: list[ExposedServer]
    unlinked_advisories: int


@router.get(
    "/vuln/supply-chain/exposure",
    response_model=SupplyChainExposureResponse,
)
def get_exposure(
    top: int = 20,
    db: Session = Depends(get_session),
) -> SupplyChainExposureResponse:
    exposure = get_supply_chain_exposure(db, top=top)
    return SupplyChainExposureResponse(**exposure)