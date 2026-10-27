from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

try:
    from .logic import get_severity_matrix
except ImportError:
    try:
        from .logic import get_advisory_severity_matrix as get_severity_matrix
    except ImportError:
        from .logic import fetch_severity_matrix as get_severity_matrix


router = APIRouter(prefix="/api", tags=["advisories"])


class FeedTotal(BaseModel):
    feed: str
    count: int


class SeverityMatrixResponse(BaseModel):
    total: int
    severities: list[str]
    ecosystems: list[str]
    matrix: dict[str, dict[str, int]]
    feeds: list[FeedTotal]
    age_buckets: dict[str, int]
    last_published_at: str | None


@router.get(
    "/advisories/severity-matrix",
    response_model=SeverityMatrixResponse,
)
async def advisory_severity_matrix(
    limit: int = Query(default=5000, ge=1, le=5000),
    feed: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> Any:
    result = get_severity_matrix(session, limit=limit, feed=feed)
    if inspect.isawaitable(result):
        result = await result
    return result


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

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
                    published_at DATETIME,
                    fetched_at DATETIME,
                    content_hash TEXT,
                    identities TEXT
                )
                """
            )
        )

        now = datetime.now(timezone.utc)
        rows = [
            {
                "id": 1,
                "feed": "feed-a",
                "summary": "Critical advisory in npm",
                "severity": "critical",
                "ecosystem": "npm",
                "package": "example-a",
                "affected_ranges": "[]",
                "aliases": "[]",
                "source_url": "https://example.invalid/a",
                "published_at": now,
                "fetched_at": now,
                "content_hash": "hash-a",
                "identities": "[]",
            },
            {
                "id": 2,
                "feed": "feed-b",
                "summary": "Critical advisory in PyPI",
                "severity": "critical",
                "ecosystem": "pypi",
                "package": "example-b",
                "affected_ranges": "[]",
                "aliases": "[]",
                "source_url": "https://example.invalid/b",
                "published_at": now - timedelta(days=10),
                "fetched_at": now,
                "content_hash": "hash-b",
                "identities": "[]",
            },
            {
                "id": 3,
                "feed": "feed-b",
                "summary": "Old low-severity advisory",
                "severity": "low",
                "ecosystem": "npm",
                "package": "example-c",
                "affected_ranges": "[]",
                "aliases": "[]",
                "source_url": "https://example.invalid/c",
                "published_at": now - timedelta(days=120),
                "fetched_at": now,
                "content_hash": "hash-c",
                "identities": "[]",
            },
        ]
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories (
                    id, feed, summary, severity, ecosystem, package,
                    affected_ranges, aliases, source_url, published_at,
                    fetched_at, content_hash, identities
                ) VALUES (
                    :id, :feed, :summary, :severity, :ecosystem, :package,
                    :affected_ranges, :aliases, :source_url, :published_at,
                    :fetched_at, :content_hash, :identities
                )
                """
            ),
            rows,
        )

    def override_get_session():
        db_session = TestSession()
        try:
            yield db_session
        finally:
            db_session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/advisories/severity-matrix",
        params={"limit": 5000},
    )
    response.raise_for_status()
    payload = response.json()

    assert payload["total"] == 3
    assert sum(payload["matrix"].get("critical", {}).values()) == 2
    assert sum(payload["age_buckets"].values()) == 3
    assert len(payload["feeds"]) == 2

    print("PASS")