from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["ask"])


class DriftResponse(BaseModel):
    drift_score: float
    changed_terms: list[str]
    term_counts: dict[str, int]


def health() -> dict:
    return {"status": "ok"}


def compute_drift_endpoint(db: Session) -> DriftResponse:
    from .logic import compute_corpus_drift
    return compute_corpus_drift(db)