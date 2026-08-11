# services/staged/ask_retrieval_service/logic.py
from __future__ import annotations

import re
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry, Base

router = APIRouter(prefix="/api")


def _tokenize(text: str) -> set[str]:
    """Simple word tokenizer – lower‑cases and extracts alphanumerics."""
    return set(re.findall(r"\w+", text.lower()))


def _score_query(
    query_terms: set[str],
    doc_terms: list[str] | None,
    snippet: str | None,
) -> int:
    """Calculate a very simple relevance score."""
    doc_terms_set = set(doc_terms or [])
    snippet_terms = _tokenize(snippet or "")
    overlap_terms = query_terms.intersection(doc_terms_set)
    overlap_snippet = query_terms.intersection(snippet_terms)
    return len(overlap_terms) + len(overlap_snippet)


class AskRetrievalResult(BaseModel):
    server_id: str
    name: str
    snippet: str
    relevance_score: int
    risk_tier: str | None
    verdict: str | None


class AskRetrievalResponse(BaseModel):
    query: str
    total: int
    results: List[AskRetrievalResult]


def get_ask_retrieval(
    query: str,
    limit: int = 10,
    db: Session = Depends(get_session),
) -> AskRetrievalResponse:
    if not query:
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")
    query_terms = _tokenize(query)

    # Join AskCorpusDoc with McpServerRegistry on server_id
    rows = (
        db.query(AskCorpusDoc, McpServerRegistry)
        .join(McpServerRegistry, AskCorpusDoc.server_id == McpServerRegistry.server_id)
        .all()
    )

    scored = []
    for doc, server in rows:
        score = _score_query(query_terms, doc.terms, doc.snippet)
        scored.append(
            {
                "server_id": server.server_id,
                "name": server.name,
                "snippet": doc.snippet,
                "relevance_score": score,
                "risk_tier": server.risk_tier,
                "verdict": server.verdict,
            }
        )

    # Sort by relevance_score descending, then server_id for stability
    scored.sort(key=lambda x: (-x["relevance_score"], x["server_id"]))

    total = len(scored)
    limited = scored[: limit if limit is not None else total]

    return AskRetrievalResponse(
        query=query,
        total=total,
        results=[AskRetrievalResult(**item) for item in limited],
    )


@router.get(
    "/ask/search",
    response_model=AskRetrievalResponse,
    summary="Search the Ask corpus",
)
def search_endpoint(
    q: str,
    limit: int = 10,
    db: Session = Depends(get_session),
):
    return get_ask_retrieval(q, limit, db)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build an in‑memory SQLite DB that mimics the real schema
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Dependency override
    def get_test_session() -> Session:  # pragma: no cover
        return TestSession()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed data
    with TestSession() as sess:
        srv1 = McpServerRegistry(
            server_id="srv1",
            name="Alpha Server",
            risk_tier="high",
            verdict="malicious",
        )
        srv2 = McpServerRegistry(
            server_id="srv2",
            name="Beta Server",
            risk_tier="medium",
            verdict="suspicious",
        )
        srv3 = McpServerRegistry(
            server_id="srv3",
            name="Gamma Server",
            risk_tier="low",
            verdict="clean",
        )
        sess.add_all([srv1, srv2, srv3])

        doc1 = AskCorpusDoc(
            content_hash="h1",
            indexed_at=datetime.datetime.utcnow(),
            server_id="srv1",
            snippet="Alpha beta gamma delta.",
            terms=["alpha", "delta"],
        )
        doc2 = AskCorpusDoc(
            content_hash="h2",
            indexed_at=datetime.datetime.utcnow(),
            server_id="srv2",
            snippet="Epsilon zeta eta theta.",
            terms=["epsilon", "theta"],
        )
        doc3 = AskCorpusDoc(
            content_hash="h3",
            indexed_at=datetime.datetime.utcnow(),
            server_id="srv3",
            snippet="Alpha epsilon iota.",
            terms=["alpha", "epsilon"],
        )
        sess.add_all([doc1, doc2, doc3])
        sess.commit()

    client = TestClient(app)

    # Perform request
    resp = client.get("/api/ask/search?q=alpha&limit=2")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["query"] == "alpha"
    assert data["total"] == 3
    assert len(data["results"]) == 2
    # The top result should be srv1 (higher overlap)
    top_server_id = data["results"][0]["server_id"]
    assert top_server_id == "srv1", f"Top result server_id {top_server_id} != 'srv1'"

    print("PASS")