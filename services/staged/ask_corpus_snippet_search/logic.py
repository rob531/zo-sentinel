"""Data and computation layer for the ask corpus snippet search service."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc


DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def _as_terms(value: Any) -> list[str]:
    """Return the terms column as a list of strings for JSON or ORM values."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            decoded = value
        if isinstance(decoded, str):
            return [decoded]
        if isinstance(decoded, (list, tuple, set)):
            return [str(item) for item in decoded if item is not None]
        return [str(decoded)]

    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def search_corpus_snippets(
    db: Session,
    server_id: str | None = None,
    term: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Search indexed corpus snippets using exact server and text filters."""
    bounded_limit = max(0, min(int(limit), MAX_LIMIT))
    if bounded_limit == 0:
        return []

    query = select(AskCorpusDoc)
    if server_id is not None:
        query = query.where(AskCorpusDoc.server_id == server_id)
    query = query.order_by(AskCorpusDoc.indexed_at.desc())

    rows = db.execute(query).scalars().all()
    needle = term.casefold() if term is not None else None

    results: list[dict[str, Any]] = []
    for row in rows:
        terms = _as_terms(row.terms)
        if needle is not None:
            snippet_matches = needle in (row.snippet or "").casefold()
            term_matches = any(needle in value.casefold() for value in terms)
            if not (snippet_matches or term_matches):
                continue

        results.append(
            {
                "server_id": row.server_id,
                "snippet": row.snippet,
                "terms": terms,
                "content_hash": row.content_hash,
                "indexed_at": row.indexed_at,
            }
        )
        if len(results) >= bounded_limit:
            break

    return results


search_snippets = search_corpus_snippets


def _self_test() -> None:
    from datetime import timedelta

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
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as db:
        db.add_all(
            [
                AskCorpusDoc(
                    server_id="s1",
                    snippet="first corpus snippet",
                    terms=["alpha", "example"],
                    content_hash="hash-s1",
                    indexed_at=datetime(2024, 1, 1) + timedelta(minutes=2),
                ),
                AskCorpusDoc(
                    server_id="s2",
                    snippet="second corpus snippet",
                    terms=["beta", "example"],
                    content_hash="hash-s2",
                    indexed_at=datetime(2024, 1, 1) + timedelta(minutes=1),
                ),
                AskCorpusDoc(
                    server_id="s3",
                    snippet="third corpus snippet",
                    terms=["gamma"],
                    content_hash="hash-s3",
                    indexed_at=datetime(2024, 1, 1),
                ),
            ]
        )
        db.commit()

    that_app = FastAPI()

    @that_app.get("/api/ask/snippets")
    def ask_snippets(
        server_id: str | None = None,
        term: str | None = None,
        limit: int = DEFAULT_LIMIT,
        db: Session = Depends(get_session),
    ) -> dict[str, Any]:
        snippets = search_corpus_snippets(db, server_id, term, limit)
        return {
            "server_id": server_id,
            "term": term,
            "limit": limit,
            "count": len(snippets),
            "snippets": snippets,
        }

    def override_get_session():
        with TestingSession() as db:
            yield db

    that_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(that_app)

    response = client.get("/api/ask/snippets", params={"server_id": "s1"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == len(payload["snippets"]) == 1
    assert payload["snippets"][0]["server_id"] == "s1"

    response = client.get("/api/ask/snippets", params={"term": "example"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == len(payload["snippets"]) == 2
    assert {row["server_id"] for row in payload["snippets"]} == {"s1", "s2"}

    print("PASS")


if __name__ == "__main__":
    _self_test()