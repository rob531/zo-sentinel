from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Dict

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _effective_limit(limit: int) -> int:
    return min(max(int(limit), 1), MAX_LIMIT)


def _extract_terms(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return [term for term in value if isinstance(term, str)]


def get_terms_frequency(
    limit: int = DEFAULT_LIMIT,
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    effective_limit = _effective_limit(limit)
    rows = db.execute(
        select(AskCorpusDoc.server_id, AskCorpusDoc.terms)
    ).all()

    term_counts: Counter[str] = Counter()
    term_servers: Dict[str, set[Any]] = defaultdict(set)

    for server_id, raw_terms in rows:
        for term in _extract_terms(raw_terms):
            term_counts[term] += 1
            term_servers[term].add(server_id)

    ordered_terms = sorted(
        term_counts,
        key=lambda term: (-term_counts[term], term),
    )

    return {
        "total_terms": len(term_counts),
        "limit": effective_limit,
        "terms": [
            {
                "term": term,
                "count": term_counts[term],
                "server_count": len(term_servers[term]),
            }
            for term in ordered_terms[:effective_limit]
        ],
    }


def _run_self_test() -> None:
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    indexed_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with TestSession() as db:
        db.add_all(
            [
                AskCorpusDoc(
                    server_id="server-1",
                    snippet="Authentication and OAuth documentation",
                    terms=["auth", "oauth"],
                    content_hash="a" * 64,
                    indexed_at=indexed_at,
                ),
                AskCorpusDoc(
                    server_id="server-2",
                    snippet="Authentication documentation",
                    terms=["auth"],
                    content_hash="b" * 64,
                    indexed_at=indexed_at,
                ),
                AskCorpusDoc(
                    server_id="server-3",
                    snippet="Database documentation",
                    terms=["db"],
                    content_hash="c" * 64,
                    indexed_at=indexed_at,
                ),
            ]
        )
        db.commit()

    def override_get_session():
        with TestSession() as db:
            yield db

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/ask-corpus/terms-frequency")
    def terms_frequency_endpoint(
        limit: int = DEFAULT_LIMIT,
        db: Session = Depends(get_session),
    ):
        return get_terms_frequency(limit=limit, db=db)

    with TestClient(that_app) as client:
        response = client.get(
            "/api/ask-corpus/terms-frequency",
            params={"limit": 2},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_terms"] == 3
    assert payload["limit"] == 2

    auth = next(
        (item for item in payload["terms"] if item["term"] == "auth"),
        None,
    )
    assert auth is not None
    assert auth["count"] == 2
    assert auth["server_count"] == 2

    counts = [item["count"] for item in payload["terms"]]
    assert counts == sorted(counts, reverse=True)
    assert len(payload["terms"]) <= payload["limit"]

    print("PASS")


if __name__ == "__main__":
    _run_self_test()