from __future__ import annotations

import json
from collections import Counter
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc


def _percentile(values: list[int], percentile: float) -> float:
    if not values:
        return 0.0

    position = (len(values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def _term_count(value: Any) -> int:
    if value is None:
        return 0

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return 0

    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return 1


def get_corpus_stats(db: Session = Depends(get_session)) -> dict[str, Any]:
    rows = db.execute(
        select(
            AskCorpusDoc.server_id,
            AskCorpusDoc.snippet,
            AskCorpusDoc.terms,
            AskCorpusDoc.content_hash,
        ).select_from(AskCorpusDoc)
    ).all()

    doc_count = len(rows)
    server_ids = {
        row.server_id
        for row in rows
        if row.server_id is not None
    }
    lengths = sorted(
        len(row.snippet)
        for row in rows
        if isinstance(row.snippet, str)
    )

    if lengths:
        length_stats = {
            "min": lengths[0],
            "p25": _percentile(lengths, 0.25),
            "median": _percentile(lengths, 0.50),
            "p75": _percentile(lengths, 0.75),
            "max": lengths[-1],
        }
    else:
        length_stats = {
            "min": 0,
            "p25": 0.0,
            "median": 0.0,
            "p75": 0.0,
            "max": 0,
        }

    buckets = {
        "short": sum(length < 40 for length in lengths),
        "medium": sum(40 <= length <= 120 for length in lengths),
        "long": sum(length > 120 for length in lengths),
    }
    length_stats["buckets"] = buckets

    total_terms = sum(_term_count(row.terms) for row in rows)
    hash_counts = Counter(
        row.content_hash
        for row in rows
        if row.content_hash is not None and row.content_hash != ""
    )

    return {
        "doc_count": doc_count,
        "server_count": len(server_ids),
        "length": length_stats,
        "avg_terms_per_doc": total_terms / doc_count if doc_count else 0.0,
        "duplicate_hash_groups": sum(
            count > 1 for count in hash_counts.values()
        ),
    }


get_snippet_length_stats = get_corpus_stats
get_corpus_shape_stats = get_corpus_stats
get_stats = get_corpus_stats


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE ask_corpus_index (
                    server_id VARCHAR NOT NULL,
                    snippet TEXT NOT NULL,
                    terms TEXT,
                    content_hash VARCHAR NOT NULL,
                    indexed_at TIMESTAMP
                )
                """
            )
        )

    test_session_factory = sessionmaker(bind=test_engine)
    test_rows = [
        (
            "00000000-0000-0000-0000-000000000001",
            "a" * 10,
            '["alpha", "beta"]',
            "hash-1",
        ),
        (
            "00000000-0000-0000-0000-000000000001",
            "b" * 20,
            '["alpha"]',
            "hash-2",
        ),
        (
            "00000000-0000-0000-0000-000000000002",
            "c" * 30,
            '["alpha", "beta", "gamma"]',
            "hash-3",
        ),
        (
            "00000000-0000-0000-0000-000000000002",
            "d" * 50,
            "[]",
            "hash-4",
        ),
        (
            "00000000-0000-0000-0000-000000000003",
            "e" * 80,
            '["alpha", "beta"]',
            "hash-5",
        ),
        (
            "00000000-0000-0000-0000-000000000004",
            "f" * 120,
            '["alpha"]',
            "hash-1",
        ),
    ]
    with test_session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO ask_corpus_index
                    (server_id, snippet, terms, content_hash, indexed_at)
                VALUES
                    (:server_id, :snippet, :terms, :content_hash, CURRENT_TIMESTAMP)
                """
            ),
            [
                {
                    "server_id": server_id,
                    "snippet": snippet,
                    "terms": terms,
                    "content_hash": content_hash,
                }
                for server_id, snippet, terms, content_hash in test_rows
            ],
        )
        session.commit()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/self-test")
    def self_test(session: Session = Depends(get_session)):
        return get_corpus_stats(session)

    with TestClient(that_app) as client:
        response = client.get("/self-test")
        payload = response.json()

        assert response.status_code == 200
        assert payload["doc_count"] == 6
        assert payload["server_count"] == 4
        assert payload["duplicate_hash_groups"] == 1
        assert 35 <= payload["length"]["median"] <= 45
        assert payload["length"]["buckets"] == {
            "short": 3,
            "medium": 3,
            "long": 0,
        }
        assert payload["avg_terms_per_doc"] == 1.5

    print("PASS")