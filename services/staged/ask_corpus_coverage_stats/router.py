"""HTTP surface for ask-corpus coverage statistics."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import corpus_coverage_stats

router = APIRouter(prefix="/api", tags=["ask-corpus-coverage-stats"])


class MissingDocsServer(BaseModel):
    server_id: str
    name: str | None = None


class CorpusServerCoverage(BaseModel):
    server_id: str
    snippet_count: int
    term_count: int
    last_indexed: datetime | None = None


class CorpusCoverageStatsResponse(BaseModel):
    servers_with_docs: int
    total_snippets: int
    total_terms: int
    avg_snippets_per_server: float
    servers_missing_docs: list[MissingDocsServer]
    top_servers: list[CorpusServerCoverage]


@router.get(
    "/ask/corpus/coverage",
    response_model=CorpusCoverageStatsResponse,
)
def get_corpus_coverage_stats(
    min_snippets: int = Query(default=1, ge=0),
    db: Session = Depends(get_session),
) -> CorpusCoverageStatsResponse:
    return corpus_coverage_stats(db, min_snippets=min_snippets)