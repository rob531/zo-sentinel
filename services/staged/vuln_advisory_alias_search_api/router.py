from __future__ import annotations

import inspect
import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import VulnAdvisory

try:
    from .logic import search_aliases as _search_aliases
except ImportError:
    try:
        from .logic import search_vuln_advisory_aliases as _search_aliases
    except ImportError:
        from .logic import search_advisory_aliases as _search_aliases


router = APIRouter(prefix="/api", tags=["vulnerability-advisories"])


class AliasSearchResult(BaseModel):
    id: int | str
    feed: str | None = None
    summary: str | None = None
    severity: str | None = None
    ecosystem: str | None = None
    package: str | None = None
    aliases: list[str] = []
    source_url: str | None = None
    published_at: Any = None


class AliasSearchResponse(BaseModel):
    query: str
    total: int
    results: list[AliasSearchResult]


def _call_search(session: Any, query: str, limit: int) -> Any:
    function = _search_aliases
    parameters = inspect.signature(function).parameters
    kwargs: dict[str, Any] = {}

    if "query" in parameters:
        kwargs["query"] = query
    elif "q" in parameters:
        kwargs["q"] = query

    if "session" in parameters:
        kwargs["session"] = session
    elif "db" in parameters:
        kwargs["db"] = session
    elif "db_session" in parameters:
        kwargs["db_session"] = session

    if "limit" in parameters:
        kwargs["limit"] = limit

    if kwargs:
        return function(**kwargs)
    return function(session, query, limit)


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    if hasattr(row, "__dict__"):
        return dict(row.__dict__)
    return dict(row)


def _normalise_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, BaseModel):
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump()
        else:
            raw = raw.dict()

    if isinstance(raw, dict):
        payload = dict(raw)
        results = payload.get("results", payload.get("items", []))
        payload["results"] = [_row_to_dict(row) for row in results]
        payload.setdefault("total", len(payload["results"]))
        payload.setdefault("query", "")
        return payload

    if isinstance(raw, tuple) and len(raw) == 2:
        first, second = raw
        if isinstance(first, (list, tuple)):
            results, total = first, second
        else:
            total, results = first, second
        return {
            "query": "",
            "total": int(total),
            "results": [_row_to_dict(row) for row in results],
        }

    if isinstance(raw, (list, tuple)):
        results = [_row_to_dict(row) for row in raw]
        return {"query": "", "total": len(results), "results": results}

    raise TypeError("Alias search logic returned an unsupported payload")


def _validate(payload: dict[str, Any]) -> AliasSearchResponse:
    if hasattr(AliasSearchResponse, "model_validate"):
        return AliasSearchResponse.model_validate(payload)
    return AliasSearchResponse.parse_obj(payload)


@router.get(
    "/vuln/aliases/search",
    response_model=AliasSearchResponse,
    summary="Search vulnerability advisory aliases",
)
async def search_vuln_advisory_aliases(
    q: str = Query(..., min_length=1, description="Alias name to search for"),
    limit: int = Query(50, ge=1, le=500),
    session: Any = Depends(get_session),
) -> AliasSearchResponse:
    raw = _call_search(session, q, limit)
    if inspect.isawaitable(raw):
        raw = await raw
    payload = _normalise_payload(raw)
    payload["query"] = q
    payload["total"] = int(payload.get("total", 0))
    return _validate(payload)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    feed TEXT,
                    summary TEXT,
                    severity TEXT,
                    ecosystem TEXT,
                    package TEXT,
                    affected_ranges TEXT,
                    aliases TEXT,
                    source_url TEXT,
                    published_at TEXT,
                    fetched_at TEXT,
                    identities TEXT,
                    content_hash TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories (
                    id, feed, summary, severity, ecosystem, package,
                    affected_ranges, aliases, source_url, published_at,
                    fetched_at, identities, content_hash
                ) VALUES (
                    :id, :feed, :summary, :severity, :ecosystem, :package,
                    :affected_ranges, :aliases, :source_url, :published_at,
                    :fetched_at, :identities, :content_hash
                )
                """
            ),
            [
                {
                    "id": 101,
                    "feed": "test-feed",
                    "summary": "Example advisory",
                    "severity": "HIGH",
                    "ecosystem": "PyPI",
                    "package": "example-package",
                    "affected_ranges": json.dumps([]),
                    "aliases": json.dumps(["GHSA-abcd-1234"]),
                    "source_url": "https://example.test/advisory/101",
                    "published_at": "2024-01-01T00:00:00",
                    "fetched_at": "2024-01-02T00:00:00",
                    "identities": json.dumps([]),
                    "content_hash": "hash-101",
                },
                {
                    "id": 102,
                    "feed": "test-feed",
                    "summary": "Another advisory",
                    "severity": "CRITICAL",
                    "ecosystem": "npm",
                    "package": "another-package",
                    "affected_ranges": json.dumps([]),
                    "aliases": json.dumps(["CVE-2024-1111"]),
                    "source_url": "https://example.test/advisory/102",
                    "published_at": "2024-02-01T00:00:00",
                    "fetched_at": "2024-02-02T00:00:00",
                    "identities": json.dumps([]),
                    "content_hash": "hash-102",
                },
                {
                    "id": 103,
                    "feed": "test-feed",
                    "summary": "Advisory without aliases",
                    "severity": "MEDIUM",
                    "ecosystem": "Go",
                    "package": "no-alias-package",
                    "affected_ranges": json.dumps([]),
                    "aliases": json.dumps([]),
                    "source_url": "https://example.test/advisory/103",
                    "published_at": "2024-03-01T00:00:00",
                    "fetched_at": "2024-03-02T00:00:00",
                    "identities": json.dumps([]),
                    "content_hash": "hash-103",
                },
            ],
        )

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/vuln/aliases/search",
            params={"q": "GHSA-abcd-1234", "limit": 50},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["query"] == "GHSA-abcd-1234"
        assert body["total"] == 1
        assert len(body["results"]) == 1
        assert str(body["results"][0]["id"]) == "101"
        assert "severity" in body["results"][0]
        assert "package" in body["results"][0]

    print("PASS")