"""HTTP surface for vulnerability match-basis provenance reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_match_basis_breakdown

router = APIRouter(prefix="/api", tags=["vuln"])


class MatchBasisEntry(BaseModel):
    match_basis: str
    count: int
    mean_confidence: float


class EcosystemEntry(BaseModel):
    ecosystem: str
    count: int


class ServerEntry(BaseModel):
    server_id: str
    count: int


class MatchBasisBreakdownResponse(BaseModel):
    total_links: int
    by_match_basis: list[MatchBasisEntry]
    by_severity: dict[str, int]
    by_ecosystem: list[EcosystemEntry]
    confidence_buckets: dict[str, int]
    top_servers: list[ServerEntry]
    generated_at: str


@router.get(
    "/vuln/match-basis",
    response_model=MatchBasisBreakdownResponse,
)
def get_match_basis(
    db: Session = Depends(get_session),
) -> MatchBasisBreakdownResponse:
    return MatchBasisBreakdownResponse.model_validate(
        get_match_basis_breakdown(db)
    )