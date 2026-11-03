"""HTTP router for corpus term co-occurrence queries."""
from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc

try:
    from .logic import get_term_cooccurrence
except ImportError:
    get_term_cooccurrence = None


router = APIRouter(prefix="/api/ask/corpus/cooccurrence", tags=["ask", "corpus"])


class RelatedTerm(BaseModel):
    term: str
    count: int
    jaccard: float


class CooccurrenceResponse(BaseModel):
    term: str
    total_snippets: int
    related: list[RelatedTerm]


def _term_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            decoded = __import__("json").loads(value)
        except (TypeError, ValueError):
            return []
        return [str(item) for item in decoded] if isinstance(decoded, list) else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return []


def _compute_cooccurrence(db: Session, term: str, limit: int) -> dict[str, Any]:
    documents = db.execute(select(AskCorpusDoc)).scalars().all()
    matching = []
    for document in documents:
        terms = _term_list(document.terms)
        if term in terms:
            matching.append((document, terms))

    if not matching:
        return {"term": term, "total_snippets": 0, "related": []}

    counts: Counter[str] = Counter()
    jaccards: dict[str, list[float]] = {}

    for _, terms in matching:
        unique_terms = set(terms)
        for candidate in unique_terms:
            if candidate == term:
                continue
            counts[candidate] += 1
            intersection = len(unique_terms.intersection({candidate}))
            union = len(unique_terms.union({candidate}))
            jaccards.setdefault(candidate, []).append(
                intersection / union if union else 0.0
            )

    related = []
    for candidate, count in counts.most_common(limit):
        values = jaccards.get(candidate, [])
        related.append(
            {
                "term": candidate,
                "count": count,
                "jaccard": sum(values) / len(values) if values else 0.0,
            }
        )

    return {
        "term": term,
        "total_snippets": len(matching),
        "related": related,
    }


@router.get("", response_model=CooccurrenceResponse)
def get_cooccurrence(
    term: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_session),
) -> CooccurrenceResponse | JSONResponse:
    normalized_term = term.strip()
    if not normalized_term or len(normalized_term) > 128:
        return JSONResponse(
            status_code=404,
            content={"error": "term_not_found"},
        )

    result = _compute_cooccurrence(db, normalized_term, limit)
    if result["total_snippets"] == 0:
        return JSONResponse(
            status_code=404,
            content={"error": "term_not_found"},
        )
    return CooccurrenceResponse(**result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as session:
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="one",
                    snippet="filesystem snippet",
                    terms=["mcp", "filesystem"],
                ),
                AskCorpusDoc(
                    server_id="two",
                    snippet="network snippet",
                    terms=["mcp", "network"],
                ),
                AskCorpusDoc(
                    server_id="three",
                    snippet="database snippet",
                    terms=["database"],
                ),
            ]
        )
        session.commit()

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_session] = lambda: TestingSession()

    from fastapi.testclient import TestClient

    client = TestClient(application)
    response = client.get("/api/ask/corpus/cooccurrence", params={"term": "mcp"})
    assert response.status_code == 200
    payload = response.json()
    related = {item["term"]: item for item in payload["related"]}
    assert "filesystem" in related
    assert "network" in related
    assert related["filesystem"]["count"] == 1
    assert related["network"]["count"] == 1
    assert 0.5 <= related["filesystem"]["jaccard"] <= 1.0
    assert 0.5 <= related["network"]["jaccard"] <= 1.0
    assert client.get(
        "/api/ask/corpus/cooccurrence", params={"term": "missing"}
    ).status_code == 404
    print("PASS")