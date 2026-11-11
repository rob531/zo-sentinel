from __future__ import annotations

import json
from collections import Counter
from typing import Any, Iterator

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session


app = FastAPI(title="Ask Corpus Terms Shape API")


def _decode_terms(value: Any) -> list[Any]:
    """Decode the terms JSON value in Python so the query remains portable."""
    if value is None:
        return []

    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return []

    if isinstance(value, str):
        if not value.strip():
            return []
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    return value if isinstance(value, list) else []


def _term_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("term", "name", "value"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
    return str(value)


@app.get("/api/ask/corpus/terms-shape")
def corpus_terms_shape(
    threshold: int = Query(default=32, ge=0),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    rows = session.execute(
        text(
            """
            SELECT server_id, snippet, terms, content_hash, indexed_at
            FROM ask_corpus_index
            """
        )
    ).mappings()

    lengths: list[int] = []
    empty_terms_docs = 0
    oversized_terms_docs = 0
    term_counts: Counter[str] = Counter()

    for row in rows:
        terms = _decode_terms(row["terms"])
        terms_len = len(terms)
        lengths.append(terms_len)

        if terms_len == 0:
            empty_terms_docs += 1
        elif terms_len > threshold:
            oversized_terms_docs += 1

        for term in terms:
            term_counts[_term_text(term)] += 1

    doc_count = len(lengths)
    mean_terms_len = float(sum(lengths) / doc_count) if doc_count else 0.0
    max_terms_len = max(lengths, default=0)
    top_terms = [
        {"term": term, "count": count}
        for term, count in sorted(
            term_counts.items(), key=lambda item: (-item[1], item[0])
        )[:15]
    ]

    return {
        "doc_count": doc_count,
        "empty_terms_docs": empty_terms_docs,
        "oversized_terms_docs": oversized_terms_docs,
        "mean_terms_len": mean_terms_len,
        "max_terms_len": max_terms_len,
        "top_terms": top_terms,
    }


def _create_test_engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE ask_corpus_index (
                    server_id TEXT NOT NULL,
                    snippet TEXT NOT NULL,
                    terms TEXT,
                    content_hash TEXT NOT NULL,
                    indexed_at TEXT NOT NULL
                )
                """
            )
        )
    return engine


def _seed_test_data(session: Session) -> None:
    term_arrays = [
        [],
        ["alpha"],
        ["alpha", "beta", "gamma"],
        [f"term-{index % 10}" for index in range(40)],
    ]

    for index, terms in enumerate(term_arrays):
        session.execute(
            text(
                """
                INSERT INTO ask_corpus_index (
                    server_id,
                    snippet,
                    terms,
                    content_hash,
                    indexed_at
                )
                VALUES (
                    :server_id,
                    :snippet,
                    :terms,
                    :content_hash,
                    :indexed_at
                )
                """
            ),
            {
                "server_id": f"server-{index}",
                "snippet": f"snippet-{index}",
                "terms": json.dumps(terms),
                "content_hash": f"hash-{index}",
                "indexed_at": "2024-01-01T00:00:00Z",
            },
        )


def run() -> None:
    engine = _create_test_engine()
    test_session_factory = sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        autocommit=False,
    )

    def override_get_session() -> Iterator[Session]:
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    try:
        with test_session_factory() as session:
            _seed_test_data(session)
            session.commit()

        with TestClient(app) as client:
            response = client.get("/api/ask/corpus/terms-shape")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["doc_count"] == 4, payload
        assert payload["empty_terms_docs"] == 1, payload
        assert payload["max_terms_len"] == 40, payload
        assert payload["top_terms"], payload
        print("PASS")
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


if __name__ == "__main__":
    run()