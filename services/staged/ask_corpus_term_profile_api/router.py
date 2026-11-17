"""HTTP surface for the corpus term-frequency profile service."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_term_profile

router = APIRouter(prefix="/api", tags=["ask-corpus"])


class TermProfileItem(BaseModel):
    term: str
    servers: int
    mentions: int


class TermProfileTotals(BaseModel):
    terms: int
    servers: int
    snippets: int


class TermProfileResponse(BaseModel):
    terms: list[TermProfileItem]
    totals: TermProfileTotals


@router.get("/ask/corpus/terms", response_model=TermProfileResponse)
def get_term_profile_endpoint(
    db: Session = Depends(get_session),
    limit: int = Query(50, ge=1, le=200),
) -> TermProfileResponse:
    profile = get_term_profile(db, limit=limit)
    return TermProfileResponse.model_validate(profile)