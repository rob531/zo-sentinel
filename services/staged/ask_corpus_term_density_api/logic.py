from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc


router = APIRouter(prefix="/api")


class TopTerm(BaseModel):
    term: str
    doc_count: int


class RecentCorpusDocument(BaseModel):
    server_id: str
    term_count: int
    indexed_at: datetime | None


class CorpusTermDensityResponse(BaseModel):
    indexed_docs: int
    docs_with_terms: int
    term_total: int
    avg_terms_per_doc: float
    top_terms: list[TopTerm]
    recent: list[RecentCorpusDocument]


def get_corpus_term_density(db: Session, limit: int = 20) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    rows = db.execute(
        select(
            AskCorpusDoc.server_id,
            AskCorpusDoc.terms,
            AskCorpusDoc.indexed_at,
        )
    ).all()

    indexed_docs = len(rows)
    docs_with_terms = 0
    term_total = 0
    term_counts: Counter[str] = Counter()
    recent: list[dict[str, Any]] = []

    for server_id, terms, indexed_at in rows:
        valid_terms = terms if isinstance(terms, list) else []
        doc_term_count = len(valid_terms)

        if doc_term_count:
            docs_with_terms += 1
            term_total += doc_term_count
            normalized_terms = {
                term.strip().lower()
                for term in valid_terms
                if isinstance(term, str) and term.strip()
            }
            term_counts.update(normalized_terms)

        recent.append(
            {
                "server_id": server_id,
                "term_count": doc_term_count,
                "indexed_at": indexed_at,
            }
        )

    dated = [row for row in recent if row["indexed_at"] is not None]
    undated = [row for row in recent if row["indexed_at"] is None]
    dated.sort(key=lambda row: row["indexed_at"], reverse=True)

    top_terms = sorted(
        term_counts.items(),
        key=lambda item: (-item[1], item[0]),
    )[:limit]

    return {
        "indexed_docs": indexed_docs,
        "docs_with_terms": docs_with_terms,
        "term_total": term_total,
        "avg_terms_per_doc": (
            term_total / indexed_docs if indexed_docs else 0.0
        ),
        "top_terms": [
            {"term": term, "doc_count": doc_count}
            for term, doc_count in top_terms
        ],
        "recent": (dated + undated)[:limit],
    }


@router.get(
    "/ask/corpus-term-density",
    response_model=CorpusTermDensityResponse,
)
def corpus_term_density(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_corpus_term_density(db, limit)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionLocal() as session:
        session.execute(
            AskCorpusDoc.__table__.insert(),
            [
                {
                    "content_hash": "density-1",
                    "server_id": "server-a",
                    "snippet": "a",
                    "terms": ["a", "b"],
                    "indexed_at": datetime(2025, 1, 1, 0, 0, 0),
                },
                {
                    "content_hash": "density-2",
                    "server_id": "server-b",
                    "snippet": "b",
                    "terms": ["a"],
                    "indexed_at": datetime(2025, 1, 2, 0, 0, 0),
                },
                {
                    "content_hash": "density-3",
                    "server_id": "server-c",
                    "snippet": "c",
                    "terms": None,
                    "indexed_at": datetime(2025, 1, 3, 0, 0, 0),
                },
                {
                    "content_hash": "density-4",
                    "server_id": "server-d",
                    "snippet": "d",
                    "terms": [],
                    "indexed_at": datetime(2025, 1, 4, 0, 0, 0),
                },
            ],
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/ask/corpus-term-density?limit=10")
        assert response.status_code == 200
        payload = response.json()
        assert payload["indexed_docs"] == 4
        assert payload["docs_with_terms"] == 2
        assert payload["term_total"] == 3
        assert any(
            item["term"] == "a" and item["doc_count"] == 2
            for item in payload["top_terms"]
        )

    print("PASS")