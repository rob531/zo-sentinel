"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_corpus_growth

router = APIRouter(prefix="/api", tags=["ask_corpus_growth"])


class CorpusGrowthPoint(BaseModel):
    date: str
    documents: int
    distinct_servers: int


class TopServer(BaseModel):
    server_id: str
    name: str
    documents: int


class CorpusGrowthResponse(BaseModel):
    days: int
    series: list[CorpusGrowthPoint]
    total_documents: int
    unique_servers: int
    top_servers: list[TopServer]


@router.get("/ask/corpus-growth", response_model=CorpusGrowthResponse)
def get_corpus_growth_endpoint(
    days: int = Query(30),
    db: Session = Depends(get_session),
) -> CorpusGrowthResponse | JSONResponse:
    if days < 1 or days > 365:
        return JSONResponse(
            status_code=400,
            content={"error": "days must be between 1 and 365"},
        )

    result = get_corpus_growth(db, days)
    return CorpusGrowthResponse(**result)