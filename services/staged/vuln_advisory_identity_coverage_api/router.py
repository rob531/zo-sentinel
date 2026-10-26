from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory
from .logic import get_identity_coverage

router = APIRouter()


class EcosystemCoverage(BaseModel):
    ecosystem: str
    total: int
    with_identities: int


class IdentityCoverageResponse(BaseModel):
    total_advisories: int
    with_identities: int
    with_aliases: int
    identity_coverage_ratio: float
    ecosystems: list[EcosystemCoverage]


@router.get(
    "/vuln-advisories/identity-coverage",
    response_model=IdentityCoverageResponse,
)
async def identity_coverage(
    session: Session = Depends(get_session),
) -> Any:
    result = get_identity_coverage(session)
    if inspect.isawaitable(result):
        result = await result
    return result


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
    )
    VulnAdvisory.__table__.create(engine)

    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (1, "osv", "Advisory one", "high", "PyPI", "example-one", "[]", '["GHSA-one"]', "https://example.test/1", now, now, '["CVE-2024-0001"]', "hash-1"),
        (2, "osv", "Advisory two", "medium", "npm", "example-two", "[]", '["GHSA-two"]', "https://example.test/2", now, now, '["CVE-2024-0002"]', "hash-2"),
        (3, "osv", "Advisory three", "low", "PyPI", "example-three", "[]", "[]", "https://example.test/3", now, now, "[]", "hash-3"),
        (4, "osv", "Advisory four", "low", "npm", "example-four", "[]", None, "https://example.test/4", now, now, None, "hash-4"),
    ]

    with engine.begin() as connection:
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
                    "id": row[0],
                    "feed": row[1],
                    "summary": row[2],
                    "severity": row[3],
                    "ecosystem": row[4],
                    "package": row[5],
                    "affected_ranges": row[6],
                    "aliases": row[7],
                    "source_url": row[8],
                    "published_at": row[9],
                    "fetched_at": row[10],
                    "identities": row[11],
                    "content_hash": row[12],
                }
                for row in rows
            ],
        )

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router, prefix="/api")

    with TestClient(that_app) as client:
        response = client.get("/api/vuln-advisories/identity-coverage")
        response.raise_for_status()
        payload = response.json()

        assert payload["total_advisories"] == 4
        assert payload["with_identities"] == 2
        assert payload["identity_coverage_ratio"] == 0.5
        assert len(payload["ecosystems"]) == 2

    print("PASS")