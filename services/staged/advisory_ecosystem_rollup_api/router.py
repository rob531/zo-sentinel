from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

from .logic import get_ecosystem_rollup

router = APIRouter(prefix="/api")


class TopPackage(BaseModel):
    package: str
    count: int


class EcosystemRollup(BaseModel):
    ecosystem: str
    severity_counts: Dict[str, int]
    total: int
    oldest_published_at: Optional[datetime] = None
    newest_fetched_at: Optional[datetime] = None
    top_packages: List[TopPackage]


class EcosystemRollupResponse(BaseModel):
    ecosystems: List[EcosystemRollup]
    generated_at: datetime


@router.get(
    "/vuln/advisories/ecosystem-rollup",
    response_model=EcosystemRollupResponse,
)
def ecosystem_rollup(session: Session = Depends(get_session)) -> EcosystemRollupResponse:
    return get_ecosystem_rollup(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
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
                    '[]', '[]', :source_url, :published_at,
                    :fetched_at, '[]', :content_hash
                )
                """
            ),
            [
                {
                    "id": 1,
                    "feed": "test",
                    "summary": "critical advisory",
                    "severity": "critical",
                    "ecosystem": "npm",
                    "package": "alpha",
                    "source_url": "https://example.test/1",
                    "published_at": "2024-01-01T00:00:00+00:00",
                    "fetched_at": "2024-01-03T00:00:00+00:00",
                    "content_hash": "00000000-0000-0000-0000-000000000001",
                },
                {
                    "id": 2,
                    "feed": "test",
                    "summary": "high advisory",
                    "severity": "HIGH",
                    "ecosystem": "npm",
                    "package": "beta",
                    "source_url": "https://example.test/2",
                    "published_at": "2024-01-02T00:00:00+00:00",
                    "fetched_at": "2024-01-04T00:00:00+00:00",
                    "content_hash": "00000000-0000-0000-0000-000000000002",
                },
                {
                    "id": 3,
                    "feed": "test",
                    "summary": "medium advisory",
                    "severity": "medium",
                    "ecosystem": "pypi",
                    "package": "gamma",
                    "source_url": "https://example.test/3",
                    "published_at": "2024-01-03T00:00:00+00:00",
                    "fetched_at": "2024-01-05T00:00:00+00:00",
                    "content_hash": "00000000-0000-0000-0000-000000000003",
                },
                {
                    "id": 4,
                    "feed": "test",
                    "summary": "low advisory",
                    "severity": "Low",
                    "ecosystem": "pypi",
                    "package": "delta",
                    "source_url": "https://example.test/4",
                    "published_at": "2024-01-04T00:00:00+00:00",
                    "fetched_at": "2024-01-06T00:00:00+00:00",
                    "content_hash": "00000000-0000-0000-0000-000000000004",
                },
                {
                    "id": 5,
                    "feed": "test",
                    "summary": "unknown advisory",
                    "severity": "informational",
                    "ecosystem": "npm",
                    "package": "epsilon",
                    "source_url": "https://example.test/5",
                    "published_at": "2024-01-05T00:00:00+00:00",
                    "fetched_at": "2024-01-07T00:00:00+00:00",
                    "content_hash": "00000000-0000-0000-0000-000000000005",
                },
            ],
        )

    test_app = FastAPI()
    test_app.include_router(router)

    test_session = Session(engine)
    test_app.dependency_overrides[get_session] = lambda: test_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/vuln/advisories/ecosystem-rollup")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert len(payload["ecosystems"]) == 2
            assert payload["generated_at"]

            by_ecosystem = {
                item["ecosystem"]: item for item in payload["ecosystems"]
            }
            for item in payload["ecosystems"]:
                assert sum(item["severity_counts"].values()) == item["total"]
                assert set(item["severity_counts"]) <= {
                    "critical",
                    "high",
                    "medium",
                    "low",
                    "unknown",
                }

            assert by_ecosystem["npm"]["severity_counts"]["critical"] == 1
            assert by_ecosystem["npm"]["severity_counts"]["high"] == 1
            assert by_ecosystem["npm"]["severity_counts"]["unknown"] == 1
            assert by_ecosystem["pypi"]["severity_counts"]["medium"] == 1
            assert by_ecosystem["pypi"]["severity_counts"]["low"] == 1
    finally:
        test_session.close()
        engine.dispose()

    print("PASS")