"""HTTP surface for vulnerability link match-basis aggregation."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_match_basis_breakdown

router = APIRouter(prefix="/api/vuln/links", tags=["vuln"])


class MatchBasisItem(BaseModel):
    match_basis: str
    links: int
    avg_confidence: float
    by_severity: dict[str, int]


class MatchBasisResponse(BaseModel):
    bases: list[MatchBasisItem]
    total_links: int


@router.get("/match-basis", response_model=MatchBasisResponse)
def get_match_basis(
    db: Session = Depends(get_session),
) -> MatchBasisResponse:
    return MatchBasisResponse(**get_match_basis_breakdown(db))