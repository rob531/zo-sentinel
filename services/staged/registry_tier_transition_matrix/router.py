"""HTTP routes for registry tier transition analysis."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import tier_transition_matrix

router = APIRouter(
    prefix="/api",
    tags=["registry-tier-transitions"],
)


class RegistryTierTransition(BaseModel):
    registry_source: str
    tier_counts: dict[str, int]
    changed_servers: int
    total_servers: int


class RegistryTierTransitionTotals(BaseModel):
    servers: int
    changed_servers: int


class RegistryTierTransitionMatrixResponse(BaseModel):
    sources: list[RegistryTierTransition]
    totals: RegistryTierTransitionTotals


class RegistryTierTransitionSummaryResponse(BaseModel):
    servers: int
    changed_servers: int


@router.get(
    "/registry/tier-transitions",
    response_model=RegistryTierTransitionMatrixResponse,
)
def get_tier_transitions(
    db: Session = Depends(get_session),
) -> RegistryTierTransitionMatrixResponse:
    payload = tier_transition_matrix(db)
    return RegistryTierTransitionMatrixResponse(
        sources=[
            RegistryTierTransition(
                registry_source=source["registry_source"],
                tier_counts=source["tier_counts"],
                changed_servers=source["changed_servers"],
                total_servers=source["total_servers"],
            )
            for source in payload["sources"]
        ],
        totals=RegistryTierTransitionTotals(
            servers=payload["totals"]["servers"],
            changed_servers=payload["totals"]["changed_servers"],
        ),
    )


@router.get(
    "/registry/tier-transitions/summary",
    response_model=RegistryTierTransitionSummaryResponse,
)
def get_tier_transition_summary(
    db: Session = Depends(get_session),
) -> RegistryTierTransitionSummaryResponse:
    totals = tier_transition_matrix(db)["totals"]
    return RegistryTierTransitionSummaryResponse(
        servers=totals["servers"],
        changed_servers=totals["changed_servers"],
    )