from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_vuln_link_match_basis_distribution

router = APIRouter(prefix="/api")


class MatchBasisDistributionItem(BaseModel):
    match_basis: str
    count: int
    distinct_servers: int
    min_confidence: float | None
    mean_confidence: float | None
    max_confidence: float | None


class MatchBasisDistributionResponse(BaseModel):
    total_links: int
    distinct_advisories: int
    distinct_servers: int
    by_match_basis: list[MatchBasisDistributionItem]


@router.get(
    "/vuln-links/match-basis",
    response_model=MatchBasisDistributionResponse,
)
async def get_match_basis_distribution(
    session: Session = Depends(get_session),
) -> MatchBasisDistributionResponse:
    return await get_vuln_link_match_basis_distribution(session)