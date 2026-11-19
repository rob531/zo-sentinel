"""HTTP surface for corpus index gap reporting."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_corpus_index_gap

router = APIRouter(prefix="/api/ask", tags=["ask"])


class MissingServer(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None


class DuplicateHashServer(BaseModel):
    server_id: str
    content_hash: str
    count: int


class CorpusIndexGapResponse(BaseModel):
    window_days: int
    servers_total: int
    servers_indexed: int
    servers_missing: list[MissingServer]
    stale_count: int
    duplicate_hash_servers: list[DuplicateHashServer]


@router.get("/corpus-index-gap", response_model=CorpusIndexGapResponse)
def get_index_gap(
    days: int = 30,
    db: Session = Depends(get_session),
) -> CorpusIndexGapResponse:
    return CorpusIndexGapResponse(**get_corpus_index_gap(db, days))