from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc


DEFAULT_LIMIT = 25
MIN_LIMIT = 1
MAX_LIMIT = 200

_POSTGRES_QUERY = """
WITH expanded AS (
    SELECT
        d.server_id,
        terms.term
    FROM ask_corpus_index AS d
    CROSS JOIN LATERAL (
        SELECT element.value AS term
        FROM jsonb_array_elements_text(
            CASE
                WHEN jsonb_typeof(d.terms::jsonb) = 'array' THEN d.terms::jsonb
                ELSE '[]'::jsonb
            END
        ) AS element

        UNION ALL

        SELECT element.value AS term
        FROM jsonb_each_text(
            CASE
                WHEN jsonb_typeof(d.terms::jsonb) = 'object' THEN d.terms::jsonb
                ELSE '{}'::jsonb
            END
        ) AS element
    ) AS terms
    WHERE d.terms IS NOT NULL
      AND jsonb_typeof(d.terms::jsonb) IN ('array', 'object')
),
frequencies AS (
    SELECT
        term,
        COUNT(*) AS frequency,
        COUNT(DISTINCT server_id) AS doc_count
    FROM expanded
    WHERE term IS NOT NULL
      AND term <> ''
    GROUP BY term
)
SELECT
    term,
    frequency,
    doc_count,
    COUNT(*) OVER () AS total_terms
FROM frequencies
ORDER BY frequency DESC, term ASC
LIMIT :limit
"""


def _clamp_limit(limit: int) -> int:
    return max(MIN_LIMIT, min(MAX_LIMIT, int(limit)))


def _decode_terms(value: Any) -> Any:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        import json

        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _term_values(value: Any) -> Iterable[str]:
    value = _decode_terms(value)
    if value is None:
        return []

    if isinstance(value, list):
        values = value
    elif isinstance(value, dict):
        values = list(value.values())
    else:
        values = [value]

    terms: list[str] = []
    for item in values:
        if item is None:
            continue
        if isinstance(item, (dict, list)):
            import json

            item = json.dumps(item, separators=(",", ":"), sort_keys=True)
        elif isinstance(item, bool):
            item = "true" if item else "false"
        term = str(item)
        if term:
            terms.append(term)
    return terms


def _is_postgres(session: Session) -> bool:
    try:
        return session.get_bind().dialect.name == "postgresql"
    except (AttributeError, TypeError):
        bind = getattr(session, "bind", None)
        dialect = getattr(getattr(bind, "dialect", None), "name", None)
        return dialect == "postgresql"


def _top_terms_python(session: Session, limit: int) -> dict[str, Any]:
    frequencies: defaultdict[str, int] = defaultdict(int)
    servers: defaultdict[str, set[Any]] = defaultdict(set)

    rows = session.execute(
        select(AskCorpusDoc.server_id, AskCorpusDoc.terms)
    )
    for server_id, terms in rows:
        for term in _term_values(terms):
            frequencies[term] += 1
            if server_id is not None:
                servers[term].add(server_id)

    ranked = sorted(
        frequencies.items(),
        key=lambda item: (-item[1], item[0]),
    )[:limit]

    return {
        "total_terms": len(frequencies),
        "terms": [
            {"term": term, "doc_count": len(servers[term])}
            for term, _frequency in ranked
        ],
        "limit": limit,
    }


def get_top_terms(session: Session, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    limit = _clamp_limit(limit)

    if _is_postgres(session):
        rows = session.execute(text(_POSTGRES_QUERY), {"limit": limit}).all()
        total_terms = int(rows[0][3]) if rows else 0
        return {
            "total_terms": total_terms,
            "terms": [
                {"term": str(row[0]), "doc_count": int(row[2])}
                for row in rows
            ],
            "limit": limit,
        }

    return _top_terms_python(session, limit)


def get_top_terms_endpoint(
    session: Session = Depends(get_session),
    limit: int = Query(DEFAULT_LIMIT, ge=MIN_LIMIT, le=MAX_LIMIT),
) -> dict[str, Any]:
    return get_top_terms(session=session, limit=limit)


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    seed_rows = [
        {
            "server_id": "server-1",
            "snippet": "first corpus document",
            "terms": ["mcp", "python", "http"],
            "content_hash": "contract-hash-1",
            "indexed_at": datetime.now(timezone.utc),
        },
        {
            "server_id": "server-2",
            "snippet": "second corpus document",
            "terms": ["mcp", "python", "json", "api"],
            "content_hash": "contract-hash-2",
            "indexed_at": datetime.now(timezone.utc),
        },
        {
            "server_id": "server-3",
            "snippet": "third corpus document",
            "terms": ["mcp", "sqlite", "json", "api"],
            "content_hash": "contract-hash-3",
            "indexed_at": datetime.now(timezone.utc),
        },
    ]
    with engine.begin() as connection:
        connection.execute(insert(AskCorpusDoc.__table__), seed_rows)

    test_app = FastAPI()

    @test_app.get("/api/ask-corpus/top-terms")
    def top_terms_route(
        limit: int = Query(DEFAULT_LIMIT, ge=MIN_LIMIT, le=MAX_LIMIT),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_top_terms(session=session, limit=limit)

    def override_session():
        with testing_session() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    response = TestClient(test_app).get(
        "/api/ask-corpus/top-terms",
        params={"limit": 5},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["limit"] == 5
    assert len(payload["terms"]) == 5
    assert any(
        item["term"] == "mcp" and item["doc_count"] == 3
        for item in payload["terms"]
    )
    print("PASS")