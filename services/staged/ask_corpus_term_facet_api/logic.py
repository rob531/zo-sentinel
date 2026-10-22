from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc


def get_term_facets(session: Session, limit: int = 50) -> dict[str, Any]:
    """Return term facets for the indexed corpus."""
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError):
        normalized_limit = 50
    normalized_limit = max(1, min(normalized_limit, 200))

    statement = select(
        AskCorpusDoc.server_id,
        AskCorpusDoc.terms,
        AskCorpusDoc.content_hash,
    )
    rows = session.execute(statement).all()

    total_docs = len(rows)
    skipped_rows = 0
    total_terms = 0
    term_counts: Counter[str] = Counter()

    for row in rows:
        terms = getattr(row, "terms", None)
        if not isinstance(terms, list):
            skipped_rows += 1
            continue

        for term in terms:
            if not isinstance(term, str):
                continue
            term_counts[term] += 1
            total_terms += 1

    facets = [
        {"term": term, "doc_count": count}
        for term, count in sorted(
            term_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]

    return {
        "total_docs": total_docs,
        "total_terms": total_terms,
        "skipped_rows": skipped_rows,
        "terms": facets[:normalized_limit],
    }


def get_corpus_term_facets(session: Session, limit: int = 50) -> dict[str, Any]:
    return get_term_facets(session, limit)


def get_term_facet(session: Session, limit: int = 50) -> dict[str, Any]:
    return get_term_facets(session, limit)


if __name__ == "__main__":
    from types import SimpleNamespace

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    class _Result:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def all(self) -> list[Any]:
            return self._rows

    class _InMemorySession:
        def __init__(self) -> None:
            self.rows = [
                SimpleNamespace(
                    server_id="server-1",
                    terms=["mcp", "registry"],
                    content_hash="hash-1",
                ),
                SimpleNamespace(
                    server_id="server-2",
                    terms=["mcp", "auth"],
                    content_hash="hash-2",
                ),
                SimpleNamespace(
                    server_id="server-3",
                    terms=None,
                    content_hash="hash-3",
                ),
            ]

        def execute(self, statement: Any) -> _Result:
            return _Result(self.rows)

    in_memory_store = _InMemorySession()

    def override_get_session() -> _InMemorySession:
        return in_memory_store

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/ask/corpus/terms")
    def corpus_terms(limit: int = 50, session: Any = Depends(get_session)) -> dict[str, Any]:
        return get_term_facets(session, limit)

    response = TestClient(that_app).get("/api/ask/corpus/terms")
    payload = response.json()

    assert response.status_code == 200
    assert payload["total_docs"] == 3
    assert next(item for item in payload["terms"] if item["term"] == "mcp")["doc_count"] == 2
    assert payload["skipped_rows"] == 1

    print("PASS")