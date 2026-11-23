"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import term_coverage

router = APIRouter(prefix="/api", tags=["corpus_term_coverage"])


class TermInfo(BaseModel):
    term: str
    documents: int


class CoverageResponse(BaseModel):
    document_count: int
    empty_terms_documents: int
    distinct_terms: int
    terms: List[TermInfo]


@router.get(
    "/corpus/term-coverage",
    response_model=CoverageResponse,
    summary="Summarise term coverage in the searchable ask‑corpus",
)
def get_term_coverage(
    limit: int = Query(50, ge=1, description="Maximum number of top terms to return"),
    server_id: Optional[str] = Query(
        None, description="Restrict to a single server identifier"
    ),
    db: Session = Depends(get_session),
) -> CoverageResponse:
    """
    Return a summary of term usage across the ask‑corpus index.

    - **document_count**: total number of documents examined.
    - **empty_terms_documents**: number of documents whose `terms` column is empty or null.
    - **distinct_terms**: total number of distinct terms observed.
    - **terms**: list of the most frequent terms (up to `limit`) with their document frequencies.
    """
    result = term_coverage(db, limit=limit, server_id=server_id)
    # `result` is expected to be a dict compatible with CoverageResponse fields.
    return CoverageResponse(**result)