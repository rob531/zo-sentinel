"""HTTP surface for corpus coverage reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import corpus_coverage

router = APIRouter(prefix="/api", tags=["ask", "corpus"])


class MissingServer(BaseModel):
    server_id: str
    name: str


class AgeBuckets(BaseModel):
    lt_24h: int
    h_24_72: int
    gt_72h: int


class CorpusCoverageResponse(BaseModel):
    indexed: int
    registry: int
    coverage_pct: float
    missing: list[MissingServer]
    age_buckets: AgeBuckets


@router.get(
    "/ask/corpus/coverage",
    response_model=CorpusCoverageResponse,
)
def get_corpus_coverage(
    db: Session = Depends(get_session),
) -> CorpusCoverageResponse:
    return CorpusCoverageResponse(**corpus_coverage(db))