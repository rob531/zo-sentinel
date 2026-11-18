"""HTTP surface for the ask-corpus terms-distribution service."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc  # noqa: F401

from .logic import get_terms_distribution

router = APIRouter(prefix="/api/ask-corpus", tags=["ask-corpus"])


class TermsCountResponse(BaseModel):
    min: int
    max: int
    avg: float
    median: float


class TopTermResponse(BaseModel):
    term: str
    doc_count: int


class TermFrequencyBucketResponse(BaseModel):
    bucket: str
    count: int


class TermsDistributionResponse(BaseModel):
    total_docs: int
    distinct_servers: int
    terms_count: TermsCountResponse
    top_terms: list[TopTermResponse]
    term_freq_histogram: list[TermFrequencyBucketResponse]
    oldest_indexed_at: Optional[datetime] = None
    newest_indexed_at: Optional[datetime] = None


@router.get("/terms-distribution", response_model=TermsDistributionResponse)
def get_terms_distribution(db: Session = Depends(get_session)) -> TermsDistributionResponse:
    return TermsDistributionResponse(**get_terms_distribution(db))