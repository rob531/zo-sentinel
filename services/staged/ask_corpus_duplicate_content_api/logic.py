from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db import get_session
from app.models import AskCorpusDoc


class DuplicateGroup(BaseModel):
    content_hash: str
    document_count: int
    server_ids: list[str]
    snippet_lengths: list[int]
    max_indexed_at: str
    distinct_terms: int


class DuplicateContentResponse(BaseModel):
    duplicate_groups: int
    duplicate_documents: int
    missing_hash_documents: int
    groups: list[DuplicateGroup]


def _as_isoformat(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _term_set(value: Any) -> set[str]:
    if value is None:
        return set()

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {value}

    if isinstance(value, dict):
        return {str(key) for key in value.keys()}
    if isinstance(value, (list, tuple, set)):
        result: set[str] = set()
        for item in value:
            if isinstance(item, (dict, list, tuple, set)):
                result.update(_term_set(item))
            else:
                result.add(str(item))
        return result
    return {str(value)}


def _duplicate_groups(session: Any, limit: int) -> DuplicateContentResponse:
    limit = max(1, min(int(limit), 200))

    group_result = session.execute(
        text(
            """
            SELECT
                content_hash,
                COUNT(*) AS document_count,
                MAX(indexed_at) AS max_indexed_at
            FROM ask_corpus_index
            WHERE content_hash IS NOT NULL
              AND TRIM(CAST(content_hash AS TEXT)) <> ''
            GROUP BY content_hash
            HAVING COUNT(*) > 1
            ORDER BY document_count DESC, content_hash ASC
            """
        )
    ).mappings().all()

    missing_result = session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM ask_corpus_index
            WHERE content_hash IS NULL
               OR TRIM(CAST(content_hash AS TEXT)) = ''
            """
        )
    )
    missing_hash_documents = int(missing_result.scalar() or 0)

    duplicate_documents = sum(int(row["document_count"]) for row in group_result)
    selected_groups = group_result[:limit]

    groups: list[DuplicateGroup] = []
    for group in selected_groups:
        content_hash = str(group["content_hash"])
        rows = session.execute(
            text(
                """
                SELECT server_id, snippet, terms, indexed_at
                FROM ask_corpus_index
                WHERE content_hash = :content_hash
                ORDER BY server_id ASC
                """
            ),
            {"content_hash": content_hash},
        ).mappings().all()

        server_ids = [str(row["server_id"]) for row in rows if row["server_id"] is not None]
        snippet_lengths = [len(row["snippet"] or "") for row in rows]
        indexed_at_values = [
            _as_isoformat(row["indexed_at"])
            for row in rows
            if row["indexed_at"] is not None
        ]
        max_indexed_at = (
            max(indexed_at_values)
            if indexed_at_values
            else _as_isoformat(group["max_indexed_at"])
        )

        distinct_terms: set[str] = set()
        for row in rows:
            distinct_terms.update(_term_set(row["terms"]))

        groups.append(
            DuplicateGroup(
                content_hash=content_hash,
                document_count=int(group["document_count"]),
                server_ids=server_ids,
                snippet_lengths=snippet_lengths,
                max_indexed_at=max_indexed_at,
                distinct_terms=len(distinct_terms),
            )
        )

    return DuplicateContentResponse(
        duplicate_groups=len(groups),
        duplicate_documents=duplicate_documents,
        missing_hash_documents=missing_hash_documents,
        groups=groups,
    )


def get_duplicate_content(
    session: Any = Depends(get_session),
    limit: int = Query(default=25, ge=1, le=200),
) -> DuplicateContentResponse:
    return _duplicate_groups(session, limit)


get_duplicates = get_duplicate_content
get_duplicate_groups = get_duplicate_content
detect_duplicates = get_duplicate_content


if __name__ == "__main__":
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

    testing_app = FastAPI()

    @testing_app.get(
        "/api/ask/corpus/duplicates",
        response_model=DuplicateContentResponse,
    )
    def duplicates_endpoint(
        session: Any = Depends(get_session),
        limit: int = Query(default=25, ge=1, le=200),
    ) -> DuplicateContentResponse:
        return get_duplicate_content(session=session, limit=limit)

    def override_get_session():
        with TestingSession() as session:
            yield session

    testing_app.dependency_overrides[get_session] = override_get_session

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO ask_corpus_index
                    (server_id, snippet, terms, content_hash, indexed_at)
                VALUES
                    ('server-a', 'alpha', '["alpha", "beta"]', 'abc123', '2024-01-01T00:00:00'),
                    ('server-b', 'bravo', '["beta", "gamma"]', 'abc123', '2024-01-02T00:00:00'),
                    ('server-c', 'charlie', '["gamma"]', 'abc123', '2024-01-03T00:00:00'),
                    ('server-d', 'delta', '["delta"]', 'unique-hash', '2024-01-04T00:00:00'),
                    ('server-e', 'echo', '["echo"]', '', '2024-01-05T00:00:00')
                """
            )
        )

    with TestClient(testing_app) as client:
        response = client.get("/api/ask/corpus/duplicates")
        assert response.status_code == 200
        payload = response.json()
        assert payload["duplicate_groups"] == 1
        assert payload["duplicate_documents"] == 3
        assert payload["missing_hash_documents"] == 1
        assert len(payload["groups"]) == 1
        assert len(payload["groups"][0]["server_ids"]) == 3
        assert payload["groups"][0]["distinct_terms"] >= 1

    print("PASS")