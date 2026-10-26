from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc

from . import logic as _logic
from .logic import *  # noqa: F401,F403

router = APIRouter(prefix="/api", tags=["ask-corpus"])


class TopTermResponse(BaseModel):
    term: str
    docs: int


class BucketCountsResponse(BaseModel):
    b1: int
    b2_3: int
    b4_7: int
    b8_plus: int


class TermsDiversityResponse(BaseModel):
    total_docs: int
    distinct_terms: int
    top_terms: list[TopTermResponse]
    bucket_counts: BucketCountsResponse
    mean_terms_per_doc: float


def _flatten_terms(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return [str(term).strip() for term in value if str(term).strip()]


def _compute_terms_diversity(session: Session, limit: int) -> dict[str, Any]:
    rows = session.execute(
        select(AskCorpusDoc.server_id, AskCorpusDoc.terms)
    ).all()

    document_frequency: dict[str, int] = {}
    server_term_counts: dict[str, int] = {}
    total_terms = 0

    for server_id, raw_terms in rows:
        terms = _flatten_terms(raw_terms)
        total_terms += len(terms)
        server_term_counts[str(server_id)] = server_term_counts.get(str(server_id), 0) + len(terms)
        for term in set(terms):
            document_frequency[term] = document_frequency.get(term, 0) + 1

    top_terms = sorted(
        ({"term": term, "docs": docs} for term, docs in document_frequency.items()),
        key=lambda item: (-item["docs"], item["term"]),
    )[:limit]

    bucket_counts = {"b1": 0, "b2_3": 0, "b4_7": 0, "b8_plus": 0}
    for count in server_term_counts.values():
        if count == 1:
            bucket_counts["b1"] += 1
        elif count <= 3:
            bucket_counts["b2_3"] += 1
        elif count <= 7:
            bucket_counts["b4_7"] += 1
        else:
            bucket_counts["b8_plus"] += 1

    total_docs = len(rows)
    return {
        "total_docs": total_docs,
        "distinct_terms": len(document_frequency),
        "top_terms": top_terms,
        "bucket_counts": bucket_counts,
        "mean_terms_per_doc": total_terms / total_docs if total_docs else 0.0,
    }


def _logic_function() -> Callable[..., Any] | None:
    names = (
        "get_terms_diversity",
        "get_terms_diversity_stats",
        "get_corpus_terms_diversity",
        "calculate_terms_diversity",
        "compute_terms_diversity",
        "analyze_terms_diversity",
        "get_diversity_stats",
        "calculate_diversity_stats",
        "compute_diversity_stats",
        "get_diversity",
        "calculate_diversity",
        "compute_diversity",
    )
    for name in names:
        function = getattr(_logic, name, None)
        if callable(function):
            return function

    candidates = []
    for name, function in vars(_logic).items():
        if not callable(function) or getattr(function, "__module__", "") != _logic.__name__:
            continue
        if any(token in name.lower() for token in ("divers", "corpus_stats", "terms_stats")):
            candidates.append(function)
    return candidates[0] if candidates else None


def _get_diversity(session: Session, limit: int) -> dict[str, Any]:
    function = _logic_function()
    if function is None:
        return _compute_terms_diversity(session, limit)

    try:
        signature = inspect.signature(function)
        signature.bind(session, limit)
        result = function(session, limit)
    except TypeError:
        try:
            result = function(session, limit=limit)
        except TypeError:
            result = function(session)

    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "dict"):
        return result.dict()
    raise TypeError("logic returned an unsupported diversity result")


@router.get(
    "/ask-corpus/terms/diversity",
    response_model=TermsDiversityResponse,
    summary="Get lexical diversity statistics for the ask corpus",
)
def get_terms_diversity(
    limit: int = Query(default=25, ge=1, le=100),
    session: Session = Depends(get_session),
) -> TermsDiversityResponse:
    return TermsDiversityResponse(**_get_diversity(session, limit))


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)

    class TestSession:
        def __init__(self, session: Session) -> None:
            self.session = session

        def __call__(self) -> Session:
            return self.session

    from sqlalchemy.orm import sessionmaker

    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="server-a",
                    snippet="first corpus document",
                    terms=["alpha", "beta"],
                    content_hash="hash-a",
                    indexed_at=datetime.now(timezone.utc),
                ),
                AskCorpusDoc(
                    server_id="server-b",
                    snippet="second corpus document",
                    terms=["beta", "gamma"],
                    content_hash="hash-b",
                    indexed_at=datetime.now(timezone.utc),
                ),
                AskCorpusDoc(
                    server_id="server-c",
                    snippet="third corpus document",
                    terms=["delta"],
                    content_hash="hash-c",
                    indexed_at=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

        from fastapi import FastAPI

        that_app = FastAPI()
        that_app.include_router(router)
        that_app.dependency_overrides[get_session] = TestSession(session)

        response = TestClient(that_app).get("/api/ask-corpus/terms/diversity")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_docs"] == 3
        assert payload["distinct_terms"] == 4
        assert payload["top_terms"]
        assert set(payload["bucket_counts"]) == {"b1", "b2_3", "b4_7", "b8_plus"}

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()