"""logic.py -- corpus term profile data/computation layer."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Set

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc


DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _iter_terms(value: Any) -> Iterable[str]:
    """Yield string terms from a JSON array value."""
    if value is None:
        return
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, str):
            term = item.strip()
            if term:
                yield term


def get_term_profile(db: Session, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    """Compute distinct-server mention counts for indexed corpus terms."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(0, min(limit, MAX_LIMIT))

    rows = db.execute(
        select(AskCorpusDoc.server_id, AskCorpusDoc.terms)
    ).all()

    mentions: Dict[str, Set[str]] = {}
    for server_id, terms in rows:
        if server_id is None:
            continue
        for term in _iter_terms(terms):
            mentions.setdefault(term, set()).add(str(server_id))

    server_ids = {
        str(server_id)
        for server_id, _ in rows
        if server_id is not None
    }
    snippets = len(rows)

    ordered = sorted(
        mentions.items(),
        key=lambda item: (-len(item[1]), item[0]),
    )
    profile_terms = [
        {"term": term, "servers": len(server_ids_for_term), "mentions": len(server_ids_for_term)}
        for term, server_ids_for_term in ordered[:limit]
    ]

    return {
        "terms": profile_terms,
        "totals": {
            "terms": len(mentions),
            "servers": len(server_ids),
            "snippets": snippets,
        },
    }


def build_term_profile(db: Session, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    """Compatibility entry point for the term profile computation."""
    return get_term_profile(db, limit)


def term_profile(db: Session, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    """Compatibility entry point for the term profile computation."""
    return get_term_profile(db, limit)


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/ask/corpus/terms")
    def corpus_terms(session: Session = Depends(get_session)):
        return get_term_profile(session)

    with session_factory() as session:
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="server-a",
                    snippet="authentication and authorization",
                    terms=["authz", "authn"],
                    content_hash="hash-a",
                    indexed_at=datetime.now(timezone.utc),
                ),
                AskCorpusDoc(
                    server_id="server-b",
                    snippet="authorization policy",
                    terms=["authz", "policy"],
                    content_hash="hash-b",
                    indexed_at=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

    response = TestClient(that_app).get("/api/ask/corpus/terms")
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["servers"] == 2
    authz = next(item for item in payload["terms"] if item["term"] == "authz")
    assert authz["servers"] == 2
    print("PASS")