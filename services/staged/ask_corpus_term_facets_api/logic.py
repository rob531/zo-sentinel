from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc


def _flatten_terms(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            decoded = json.loads(text)
        except (TypeError, ValueError):
            return [text]
        if isinstance(decoded, str):
            return [decoded]
        return _flatten_terms(decoded)

    if isinstance(value, dict):
        return [key for key in value.keys() if isinstance(key, str)]

    if isinstance(value, (list, tuple, set)):
        terms: list[str] = []
        for item in value:
            terms.extend(_flatten_terms(item))
        return terms

    return []


def get_term_facets(
    db: Session,
    limit: int = 50,
    server_id: str | None = None,
) -> dict[str, Any]:
    statement = select(AskCorpusDoc.server_id, AskCorpusDoc.terms)
    if server_id is not None:
        statement = statement.where(AskCorpusDoc.server_id == server_id)

    rows = db.execute(statement).all()

    counts: defaultdict[str, int] = defaultdict(int)
    servers_by_term: defaultdict[str, set[str]] = defaultdict(set)
    scoped_servers: set[str] = set()

    for row_server_id, raw_terms in rows:
        if row_server_id is not None:
            scoped_servers.add(row_server_id)

        for raw_term in _flatten_terms(raw_terms):
            term = raw_term.lower().strip()
            if not term or len(term) > 64:
                continue
            counts[term] += 1
            if row_server_id is not None:
                servers_by_term[term].add(row_server_id)

    ordered_terms = sorted(counts, key=lambda term: (-counts[term], term))[:limit]

    return {
        "total_terms": len(counts),
        "scoped_servers": len(scoped_servers),
        "terms": [
            {
                "term": term,
                "count": counts[term],
                "server_count": len(servers_by_term[term]),
            }
            for term in ordered_terms
        ],
    }


def get_corpus_term_facets(
    db: Session,
    limit: int = 50,
    server_id: str | None = None,
) -> dict[str, Any]:
    return get_term_facets(db, limit=limit, server_id=server_id)


def list_term_facets(
    db: Session,
    limit: int = 50,
    server_id: str | None = None,
) -> dict[str, Any]:
    return get_term_facets(db, limit=limit, server_id=server_id)


def get_ask_corpus_term_facets(
    db: Session,
    limit: int = 50,
    server_id: str | None = None,
) -> dict[str, Any]:
    return get_term_facets(db, limit=limit, server_id=server_id)


def get_ask_corpus_terms(
    db: Session,
    limit: int = 50,
    server_id: str | None = None,
) -> dict[str, Any]:
    return get_term_facets(db, limit=limit, server_id=server_id)


if __name__ == "__main__":
    from datetime import datetime, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_factory() as session:
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="server-a",
                    snippet="first",
                    terms=["mcp", "tool"],
                    content_hash="hash-a",
                    indexed_at=datetime.now(timezone.utc),
                ),
                AskCorpusDoc(
                    server_id="server-b",
                    snippet="second",
                    terms=["mcp", "registry"],
                    content_hash="hash-b",
                    indexed_at=datetime.now(timezone.utc),
                ),
                AskCorpusDoc(
                    server_id="server-c",
                    snippet="third",
                    terms=None,
                    content_hash="hash-c",
                    indexed_at=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/ask/corpus/terms")
    def corpus_terms(
        limit: int = Query(default=50, ge=1, le=200),
        server_id: str | None = None,
        db: Session = Depends(get_session),
    ):
        return get_term_facets(db, limit=limit, server_id=server_id)

    with TestClient(that_app) as client:
        response = client.get("/api/ask/corpus/terms")
        assert response.status_code == 200
        payload = response.json()
        terms = {item["term"]: item for item in payload["terms"]}
        assert payload["total_terms"] == 3
        assert payload["scoped_servers"] == 3
        assert terms["mcp"]["count"] == 2
        assert terms["mcp"]["server_count"] == 2
        assert terms["registry"]["count"] == 1
        assert terms["tool"]["count"] == 1

        scoped_response = client.get(
            "/api/ask/corpus/terms",
            params={"server_id": "server-a"},
        )
        assert scoped_response.status_code == 200
        scoped_payload = scoped_response.json()
        assert scoped_payload["total_terms"] == 2
        assert scoped_payload["scoped_servers"] == 1
        assert {item["term"] for item in scoped_payload["terms"]} == {"mcp", "tool"}

    engine.dispose()
    print("PASS")