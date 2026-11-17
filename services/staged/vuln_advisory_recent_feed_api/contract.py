from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, List, Optional

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory


class AdvisoryItem(BaseModel):
    id: int
    feed: Optional[str]
    summary: Optional[str]
    severity: Optional[str]
    ecosystem: Optional[str]
    package: Optional[str]
    source_url: Optional[str]
    published_at: Optional[datetime]
    fetched_at: Optional[datetime]
    alias_count: int


class RecentAdvisoriesResponse(BaseModel):
    count: int
    items: List[AdvisoryItem]


def _alias_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, list):
        return len(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return 0
        return len(decoded) if isinstance(decoded, list) else 0
    return 0


def _build_app() -> FastAPI:
    application = FastAPI()

    @application.get(
        "/api/vuln/advisories/recent",
        response_model=RecentAdvisoriesResponse,
    )
    def recent_advisories(
        limit: int = Query(20),
        severity: Optional[str] = Query(None),
        ecosystem: Optional[str] = Query(None),
        days: Optional[int] = Query(None),
        session=Depends(get_session),
    ) -> dict:
        bounded_limit = max(1, min(100, limit))
        table_name = VulnAdvisory.__tablename__

        conditions = []
        parameters: dict[str, Any] = {"limit": bounded_limit}

        if severity is not None:
            conditions.append("severity = :severity")
            parameters["severity"] = severity

        if ecosystem is not None:
            conditions.append("LOWER(ecosystem) = LOWER(:ecosystem)")
            parameters["ecosystem"] = ecosystem

        if days is not None:
            conditions.append("published_at >= :published_cutoff")
            parameters["published_cutoff"] = (
                datetime.utcnow() - timedelta(days=days)
            ).strftime("%Y-%m-%d %H:%M:%S.%f")

        where_clause = ""
        if conditions:
            where_clause = " WHERE " + " AND ".join(conditions)

        statement = text(
            f"""
            SELECT id, feed, summary, severity, ecosystem, package,
                   source_url, published_at, fetched_at, aliases
            FROM {table_name}
            {where_clause}
            ORDER BY (published_at IS NULL) ASC, published_at DESC
            LIMIT :limit
            """
        )
        rows = session.execute(statement, parameters).mappings().all()

        items = []
        for row in rows:
            items.append(
                {
                    "id": row["id"],
                    "feed": row["feed"],
                    "summary": row["summary"],
                    "severity": row["severity"],
                    "ecosystem": row["ecosystem"],
                    "package": row["package"],
                    "source_url": row["source_url"],
                    "published_at": row["published_at"],
                    "fetched_at": row["fetched_at"],
                    "alias_count": _alias_count(row["aliases"]),
                }
            )

        return {"count": len(items), "items": items}

    return application


def run() -> int:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    table_name = VulnAdvisory.__tablename__
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE {table_name} (
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
                    identities TEXT,
                    content_hash TEXT
                )
                """
            )
        )

    now = datetime.utcnow()
    with TestingSessionLocal() as session:
        session.execute(
            text(
                f"""
                INSERT INTO {table_name} (
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
                    "id": 1,
                    "feed": "osv",
                    "summary": "Critical advisory",
                    "severity": "critical",
                    "ecosystem": "PyPI",
                    "package": "example-critical",
                    "affected_ranges": json.dumps(["<2.0"]),
                    "aliases": json.dumps(["CVE-2025-0001", "GHSA-test-0001"]),
                    "source_url": "https://example.test/critical",
                    "published_at": now - timedelta(hours=1),
                    "fetched_at": now,
                    "identities": json.dumps(["critical-1"]),
                    "content_hash": "critical-hash",
                },
                {
                    "id": 2,
                    "feed": "osv",
                    "summary": "High npm advisory",
                    "severity": "high",
                    "ecosystem": "npm",
                    "package": "example-high",
                    "affected_ranges": json.dumps(["<1.5"]),
                    "aliases": json.dumps(["CVE-2025-0002"]),
                    "source_url": "https://example.test/high",
                    "published_at": now - timedelta(hours=2),
                    "fetched_at": now,
                    "identities": json.dumps(["high-1"]),
                    "content_hash": "high-hash",
                },
                {
                    "id": 3,
                    "feed": "osv",
                    "summary": "High PyPI advisory",
                    "severity": "high",
                    "ecosystem": "pypi",
                    "package": "example-high-pypi",
                    "affected_ranges": json.dumps(["<3.0"]),
                    "aliases": json.dumps([]),
                    "source_url": "https://example.test/high-pypi",
                    "published_at": now - timedelta(hours=3),
                    "fetched_at": now,
                    "identities": json.dumps(["high-2"]),
                    "content_hash": "high-pypi-hash",
                },
            ],
        )
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    application = _build_app()
    application.dependency_overrides[get_session] = override_get_session

    with TestClient(application) as client:
        response = client.get("/api/vuln/advisories/recent")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["count"] == 3, payload
        assert payload["items"][0]["severity"] == "critical", payload
        assert {
            item["severity"] for item in payload["items"]
        } == {"critical", "high"}, payload
        assert {
            item["ecosystem"].lower() for item in payload["items"]
        } == {"pypi", "npm"}, payload

        filtered = client.get(
            "/api/vuln/advisories/recent",
            params={"severity": "critical"},
        )
        assert filtered.status_code == 200, filtered.text
        filtered_payload = filtered.json()
        assert filtered_payload["count"] == 1, filtered_payload
        assert len(filtered_payload["items"]) == 1, filtered_payload
        assert filtered_payload["items"][0]["severity"] == "critical", filtered_payload

    engine.dispose()
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())