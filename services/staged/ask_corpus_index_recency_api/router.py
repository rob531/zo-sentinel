"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import corpus_index_recency

router = APIRouter(prefix="/api/ask", tags=["ask"])


class IndexRecencyResponse(BaseModel):
    total_docs: int
    distinct_servers: int
    recent_24h: int
    recent_7d: int
    recent_30d: int
    oldest_indexed_at: str
    newest_indexed_at: str
    per_server_buckets: dict[str, int]
    stale_index_candidates: int


@router.get("/corpus/index-recency", response_model=IndexRecencyResponse)
def get_index_recency(db: Session = Depends(get_session)) -> IndexRecencyResponse:
    recency = corpus_index_recency(db)
    return IndexRecencyResponse(**recency)