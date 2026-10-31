import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc


def _parse_terms(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []

    if not isinstance(value, list):
        return []

    terms: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip().lower()
        if normalized:
            terms.append(normalized)
    return terms


def get_corpus_term_frequency(
    db: Optional[Session] = None,
    limit: int = 50,
    *,
    session: Optional[Session] = None,
) -> dict[str, Any]:
    active_session = db if db is not None else session
    if active_session is None:
        raise ValueError("A database session is required")

    try:
        clamped_limit = int(limit)
    except (TypeError, ValueError):
        clamped_limit = 50
    clamped_limit = max(1, min(200, clamped_limit))

    documents = active_session.execute(
        select(AskCorpusDoc.server_id, AskCorpusDoc.terms)
    ).all()

    term_counts: dict[str, int] = {}
    term_servers: dict[str, set[Any]] = {}
    corpus_servers: set[Any] = set()

    for server_id, raw_terms in documents:
        corpus_servers.add(server_id)
        for term in _parse_terms(raw_terms):
            term_counts[term] = term_counts.get(term, 0) + 1
            term_servers.setdefault(term, set()).add(server_id)

    ordered_terms = sorted(term_counts.items(), key=lambda item: (-item[1], item[0]))
    top_terms = [
        {
            "term": term,
            "count": count,
            "distinct_servers": len(term_servers[term]),
        }
        for term, count in ordered_terms[:clamped_limit]
    ]

    return {
        "terms": top_terms,
        "total_docs": len(documents),
        "distinct_servers": len(corpus_servers),
        "distinct_terms": len(term_counts),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


corpus_term_frequency = get_corpus_term_frequency
get_term_frequency = get_corpus_term_frequency
ask_corpus_term_frequency = get_corpus_term_frequency


if __name__ == "__main__":
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_db = TestingSessionLocal()

    test_db.add_all(
        [
            AskCorpusDoc(
                server_id="server-a",
                snippet="alpha alpha beta",
                terms=json.dumps(["Alpha", "alpha", " Beta "]),
                content_hash="hash-a",
            ),
            AskCorpusDoc(
                server_id="server-b",
                snippet="alpha gamma",
                terms=json.dumps(["alpha", "Gamma"]),
                content_hash="hash-b",
            ),
            AskCorpusDoc(
                server_id="server-c",
                snippet="gamma malformed empty",
                terms=json.dumps(["Gamma", 123, ""]),
                content_hash="hash-c",
            ),
        ]
    )
    test_db.commit()

    that_app = FastAPI()

    def override_get_session():
        yield test_db

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/ask/corpus/terms")
    def corpus_terms_endpoint(
        limit: int = 50,
        session: Session = Depends(get_session),
    ):
        return get_corpus_term_frequency(session, limit)

    with TestClient(that_app) as client:
        response = client.get("/api/ask/corpus/terms")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_docs"] == 3
        assert payload["terms"][0]["term"] == "alpha"
        assert payload["terms"][0]["count"] == 3
        returned_terms = {item["term"] for item in payload["terms"]}
        assert "123" not in returned_terms
        assert "" not in returned_terms

    test_db.close()
    test_engine.dispose()
    print("PASS")